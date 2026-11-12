# 时长分歧取证报告（隔离实例 / 只读诊断）

- 日期：2026-11-12
- 对象系统：solo-0023-voice-timeline-renderer（Go，基线提交 `37a6493`，未改一行生产代码）
- 取证方式：未修改的基线二进制，运行在**独立端口 127.0.0.1:8099 + 独立空数据目录**
  `/home/node/forensics/instance-data`；所有判断由仓库外的独立 Python（`python3 -I`，
  仅标准库）从**原始字节**得出，不依赖被检服务的解析器，不重编码任何素材。
- 工作区：`/home/node/forensics/`（`fixtures/` 证物、`scripts/` 脚本、`evidence/` 证据、
  `probe/` 基线二进制）。仓库 `git status` 全程为空。

---

## 1. 结论摘要（先看这里）

1. 分歧是**真实且可稳定复现**的，根因是 WAV 解析器对重复 `data` chunk 的处理：
   `internal/audiocodec/wav.go:42` 在 chunk 循环里对 `sampleChunk` 做**覆盖赋值**，
   一个 RIFF/WAVE 若含多个 `data` chunk，解析器只保留**最后一个**，前面的全部丢弃。
2. 因此同一份字节会产生两个互不相等的"帧数"，且各自自洽：
   - **容器事实**：所有 `data` chunk 之和 = **960 帧**；
   - **解析器可见 / 工程实际可用**：仅最后一个 chunk = **480 帧**（不均等拆分时为 360）。
3. 摘要（digest）相同只证明**字节未被改动**；它对"解析器如何解释这些字节"没有任何约束力。
   所以"摘要仍与初次登记相同"与"工程只读到 480 帧"可以同时成立，二者不矛盾。
4. 任务显示 `completed` 不代表保留了完整时长。只要时间线网格请求的区间落在解析器可见的
   480 帧之内，渲染就会成功并原子发布一个**自洽但只有一半内容**的 WAV；"完成"只表示
   状态机无错误走完，不表示输出覆盖了容器的全部 PCM。
5. 修正播放器/输出的采样率**标签**与本缺陷正交。48k 与 44.1k 两个成品的容器头都准确、
   各自自洽，但都只含最后一个 chunk；标签正确不能让丢失的前 480 帧回来，不能证明声音恢复。
6. 本系统中，若时间线真按 960 帧的源记录去请求完整区间（0..960），当前代码会让该渲染
   **失败（`failed`）且不发布任何输出**，而不是静默产出 960。因此线上"完成但只有 480"的
   成品，必然来自一个只请求了 ≤480 帧的网格（详见 §4 与 §7 的未知项）。

---

## 2. 四层"承诺"分别是什么（容器 / 源记录 / 采样网格 / 最终量化）

| 层 | 数字来自哪里 | 它承诺什么 | 它**不**承诺什么 | 证据 |
|---|---|---|---|---|
| 容器事实 | RIFF 全部 `data` chunk 字节之和（独立解析器 `ref_parse.py`） | 文件里物理存在多少 PCM 帧、fmt 速率/位深/声道、RIFF 尺寸是否覆盖全文件 | 不承诺任何应用会把全部 chunk 都读进去 | E1 |
| 源记录 `metadata.json` / 登记响应 | 登记时由**同一个有缺陷的解析器**算出的 `frame_count`、`duration_ms`，以及对**原始字节**的 SHA-256 | digest 准确描述存储字节；帧数字段只描述"解析器当时看到的帧数" | digest 不担保帧数正确；多 chunk 时帧数会少报 | E2、E6 |
| 工程采样网格 `timelines/*.json` 的 `output_frames` | 用**源记录的帧/速率**做 `ScaledFrames` 比例换算（`domain/validate.go:100,124`），纯算术 | 若源记录与字节一致，它准确预告输出帧数；跨速率按比例（480→441） | 它不重新解码字节，无法发现"记录帧数 > 解析器可见帧数"；记录错则网格跟着错 | E3、E6 |
| 最终量化事实 `renders/<id>/output.wav` + `receipt.json` | 渲染时**重新解码字节**（`prepare.go:46`）→ 切片/重采样/混音/归一化 → 16-bit 量化，再由编码端重解码回填 receipt | 成品 WAV 与 receipt 的 digest/帧数严格一致、原子发布、失败不留半成品 | 不承诺覆盖容器全部 PCM；多 chunk 源只会包含最后一个 chunk 的样本 | E4、E6、E8 |

关键：**源记录和最终渲染虽然都出自同一服务，却走了两条不同的取数路径**——网格信任
metadata 的帧数字段，渲染信任对字节的重新解码。当字节含多个 `data` chunk 时，这两条路径
对"这份源有多少帧"给出不同答案，分歧就此产生。

---

## 3. 复现到的硬事实（编号对应 evidence/）

### E1 容器事实（独立解析器，中立）
- `multi_data_960.wav`：2 个 `data` chunk（960B + 960B，mono/16bit/48k），
  合计 **1920B = 960 帧**；"最后 chunk 覆盖式"解析只得 **480 帧**。
- `multi_data_odd.wav`：2 个 `data` chunk（1200B + 720B），合计仍 **960 帧**，
  覆盖式解析只得 **360 帧**（证明丢失量随拆分变化，不是固定常数）。
- `single_normal.wav`、`stereo_normal.wav`：单 `data` chunk，合计与覆盖式都 = **960 帧**，
  无分歧（对照）。RIFF size 字段均正确覆盖全文件，fmt 各项自洽。

### E2 源登记（经真实 HTTP `PUT /v1/sources`）
| 源 | 容器真帧数 | 服务记录帧数 | 偏差 | digest==字节 |
|---|---|---|---|---|
| src_multi_960 | 960 | **480** | −480 | 是 |
| src_multi_odd | 960 | **360** | −600 | 是 |
| src_single（对照） | 960 | 960 | 0 | 是 |
| src_stereo（对照） | 960 | 960 | 0 | 是 |

多 chunk 文件被**接受为合法源（201）**，但帧/时长少报；digest 仍逐字节正确。

### E3/E4 网格与渲染
- 用 480 帧记录建 0..480 网格（48k）：网格预告 480，渲染 `completed`，**最终量化 480 帧**，
  receipt digest == 下载字节 digest（链路自洽）。
- 同源建 44.1k 网格：480 经 `ScaledFrames` → **441 帧**，渲染 `completed`，成品 441 帧、
  头速率准确 44100——证明速率换算本身正确，问题不在重采样。
- 单 chunk 对照 0..960：`completed`，成品 **960 帧**，网格==最终，delta=0。

### E6 旧登记播种（模拟"备份恢复后记录仍与初次登记相同"，字节零改动）
用同一份 multi-chunk 字节 + 一份正确记录 960 帧的 `metadata.json`：
- `GET` 源：记录**仍承诺 960 帧 / 20ms**，digest 仍与字节一致；
- 建 0..960 完整网格：成功，网格**预告 960**；
- 渲染该完整网格：任务 **`failed`（render_failed）**，`renders/` 下**无输出目录**、
  下载返回 409、`tmp/` 已清空（失败不发布半成品）；
- 建 0..480 部分网格：`completed`，成品 **480 帧**，且其 digest
  `8c1d…e914` 与普通 480 渲染**逐字节相同**。

这条直接回答了"960 预告 / 480 下载 / 任务完成"如何共存：记录与网格可以承诺 960，
但当前渲染对字节只能取 480；能"完成"的网格必然把自己限制在 480 内。

### E8 样本级归属（证明是真丢音频，不是计数显示问题）
夹具两段 chunk 数值区间不相交、符号模式不同（第一段**全负**，第二段跨正负）。
成品 480 帧输出**含正样本，符号模式与第二段完全一致、与第一段不一致**；
第一段 480 帧的全负模式在成品中完全不出现。归一化是严格正向线性缩放，不改变符号，
故可断定：**成品逐样本复刻的是最后一个 data chunk，前 480 帧音频物理缺席。**

### E9 负对照（界定检测器边界）
含良性 `LIST` 元数据 chunk + 单个 `data` chunk 的合法 WAV：登记 960 帧，
字节级分类判为 `RELEASE-CLEAN`。缺陷**只对重复 `data` chunk 成立**，不冤枉其它多 chunk。

### E5 / E10 持续受理与不可覆盖
- 重复同源写：200 幂等，digest 不变；同一 render id 改绑别的 timeline：**409 render_conflict**；
  同参重提：200 返回原任务，状态不变。
- 并发：一个线程持续登记/渲染 12 个全新合法素材，另一线程并行 6 次只读审计 + 终态审计。
  **零错误**，新增干净对象全部 `RELEASE-CLEAN`，3 个分歧源始终 `HOLD-DIVERGENT` 不翻转，
  所有重放均幂等。证明排查无需停服，后到的重制结果不会覆盖原成品。

---

## 4. 根因（代码定位，仅诊断不改）

- `internal/audiocodec/wav.go:38-43`：chunk 遍历 `switch` 中
  `case "data": sampleChunk = data[payloadStart:payloadEnd]` 为覆盖赋值，
  多 `data` chunk 时仅最后一个存活；`frameCount`（wav.go:96）据此少算。
- 同一解析器被两处复用，导致两层数字分叉：
  - 登记路径 `service/sources.go:48` → 写进源记录的帧/时长；
  - 渲染路径 `render/prepare.go:46` → 重新解码，切片 `SliceFrames` 只能在可见帧内工作。
- 网格路径 `domain/validate.go:100,124-128` 与 `TimelineOutputFrames` 只做基于**记录字段**
  的算术，不碰字节，因此会按记录的帧数去预告。
- 失败保护是有效的：`SliceFrames`（transform.go:14）在 `endFrame > 可见帧数` 时报错，
  经 `failRender` 落 `failed` 终态且 `CommitRenderArtifacts` 不发生（E6 已证）。
  即系统不会产出"声称 960 实际 480"的损坏文件；它要么失败，要么只发布请求范围内、
  但内容已被解析器截断的成品。

---

## 5. 逐对象保全 / 放行依据（只信字节，规则可重放）

脚本 `scripts/triage.py` 对数据目录只读扫描，规则：

源（source）
- `RELEASE-CLEAN`：单 `data` chunk **且** 容器帧数 == 记录帧数 **且** 媒体 digest == metadata digest。
- `HOLD-DIVERGENT`：`data` chunk 数 > 1（覆盖式解析必然丢弃前段），无论记录是否与字节相符；
  或单 chunk 但记录/容器帧数不符。
- `HOLD-TAMPER`：媒体 digest ≠ metadata digest，或不是可解析 RIFF/WAVE。

成品（render）
- `RELEASE-CLEAN`：引用的每个源都 `RELEASE-CLEAN`，且 output digest == receipt digest，
  输出帧数 == receipt 帧数。
- `HOLD-AFFECTED`：引用了任一 `HOLD-DIVERGENT` 源——成品不可能包含被丢弃的帧。
- `HOLD-INTEGRITY`：digest 不符或 artifact 缺失。

隔离目录实测（E7/E10 终态）：源 3 个 HOLD-DIVERGENT、15 个 RELEASE-CLEAN；
成品 3 个 HOLD-AFFECTED、13 个 RELEASE-CLEAN。逐对象理由与哈希均落盘在
`evidence/E7_triage_isolated.json`、`E10_concurrency.json`。

保全操作约束（本轮已遵守，建议沿用）：
- 一律**只读 + 字节级副本保全**，按 digest 命名冷备，禁止原地重编码、禁止把重编码文件当原素材；
- 分歧对象在修复规则上线前保持 HOLD，不删除、不覆盖；
- 干净对象凭上述三重一致即可继续放行，无需停服。

---

## 6. 为什么"改采样率显示"不能证明声音恢复

- 48k 成品头：`(rate=48000, ch=1, frames=480)`；44.1k 成品头：`(44100, 1, 441)`，
  头速率与 receipt、与实际字节三者一致，标签本就正确，无"显示 bug"可修。
- 真正缺的是**样本**（前一个 data chunk 的 480 帧，E8 已证其符号模式整体缺席）。
  改任何速率/时长标签都不会增加一个样本；判断声音是否恢复的唯一有效判据是：
  在能汇总全部 `data` chunk 的解析下，容器帧数、源记录帧数、网格帧数、成品帧数四者一致，
  且成品样本覆盖源的完整区间——而不是看播放器显示什么速率。

---

## 7. 当前工具/材料下无法闭合的未知项（保留为未知，不臆断）

1. **线上真实备份集的容器结构未知。** 本环境只有代码库、没有生产数据目录，多 `data` chunk
   是依据代码缺陷构造的**机理复现**，不是从线上文件实测。线上那批 WAV 是否确为多 `data`
   chunk、各 chunk 如何拆分，必须用 `triage.py`/`ref_parse.py` 对真实备份字节跑一遍才能确定。
2. **"初次登记"当时记录的到底是 960 还是 480，无法回溯。** 历史 metadata 的演化没有审计日志，
   现有工具无法证明初次登记值。E6 仅模拟了"记录仍为 960"这一种情形；若初次登记本就被缺陷
   记成 480，则属于另一条路径（记录与解析一致地少报），需真实历史 metadata 才能区分。
3. **线上"完成的 480 成品"对应的具体网格区间未知。** 本代码下完整 960 网格会失败而非完成，
   故线上成品应由 ≤480 的网格产生，但真实 timeline JSON 的 span 边界需取线上记录核对，
   不能凭机理替它断言。
4. **是否存在其它结构性变体未覆盖。** 本次验证了多 `data` chunk、良性 `LIST`、单 chunk、
   单/双声道；未穷尽带 padding 奇数字节、`fact` chunk、非 PCM、RF64 等情形。这些是否被
   接受/误读，需额外证物；在实测前不对它们下结论。
5. **重编码能否"修复"未被验证，也不应被当作恢复手段。** 一旦前段 chunk 在某次处理中已不在
   字节里，任何重编码都无法找回；是否可找回，取决于保全的原始字节里是否仍物理包含这些 chunk
   （E1 式独立解析可判），本轮不做重编码，故对此保持未知。

---

## 8. 如何重放（全部在仓库外、隔离、只读）

```bash
# 工具链（本地用户目录，无需 root）
export PATH=/home/node/toolchain/go/bin:$PATH CGO_ENABLED=0
cd /workspace
go build -buildvcs=false -o /home/node/forensics/probe/voiceservice ./cmd/voiceservice

# 证物与中立容器事实
python3 -I /home/node/forensics/scripts/make_fixtures.py
python3 -I /home/node/forensics/scripts/ref_parse.py \
  /home/node/forensics/fixtures/*.wav

# 隔离实例（独立端口/独立数据目录），再跑各实验
/home/node/forensics/probe/voiceservice -addr 127.0.0.1:8099 \
  -data /home/node/forensics/instance-data
FORENSIC_BASE=http://127.0.0.1:8099 \
  python3 -I /home/node/forensics/scripts/campaign.py
python3 -I /home/node/forensics/scripts/legacy_seed.py
python3 -I /home/node/forensics/scripts/sample_loss.py
python3 -I /home/node/forensics/scripts/negative_control.py
python3 -I /home/node/forensics/scripts/concurrency.py

# 对任意真实数据目录做只读逐对象分类（不改任何文件）
python3 -I /home/node/forensics/scripts/triage.py <数据目录>
```

本轮未改任何生产规则、未重编码任何素材、未把重编码文件当原素材；仓库工作树保持干净。
