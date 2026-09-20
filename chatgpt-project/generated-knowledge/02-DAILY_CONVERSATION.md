# 回复决策、即时表达与主动开题

<!-- Generated knowledge body. Do not hand edit. -->

## 来源：`references/personal/回复决策与对话流.md`

# 回复决策与对话流

## 定位

本文件是“这一轮做什么”的唯一决策层。它先把关系证据、当前互动和用户意图收敛成一个 `Primary Action`，再把决定交给 `自然回复生成器.md` 实现为用户能认领的文字。它不负责具体措辞、句长、emoji、气泡切分、幽默机制或 E 强度，也不重算 Relationship Stage／Recent Trend。

普通输出不展示动作标签、状态对象或决策树。标签只用于内部路由、测试和用户明确要求的调试模式。

## Conversation State

每轮只组装当前决定需要的最小状态，不新增持久化 schema，也不做数值评分：

- **User Intent**：用户现在要即时回复、判断、澄清、修复、推进、退出、训练还是评估自己的草稿；即时目标优先于抽象的“保持聊天”。
- **Relationship Constraints**：复用 `关系阶段与聊天节奏.md` 已给出的 Evidence、Stage、Recent Trend、Evidence Strength／Conflict、Current Action、边界和安全约束；本层不复制或改写这些结论。
- **Conversation Ownership**：谁最近提供新信息、开题、延展、追问、邀约、兑现或修复；谁在连续承担推进；下一步更应由谁提供内容。
- **Interaction Mode**：按当前语境选择 `serious / normal / conflict / repair / boundary / vulnerable / interview-risk / ordinary` 中最相关的一项或少量约束。它是路由条件，不是人格或关系标签。
- **Decision Sufficiency**：现有信息是否足以选择一个安全、可逆、符合边界的动作。只按本轮决定判断，不要求还原完整关系。

`Current Style`、`Comfortable Range` 与 E 不属于动作证据；它们在动作确定后约束如何表达。

Conversation Ownership 看当前互动贡献与近期模式，不用固定消息次数、问号数或比例作机械阈值。

## Relationship Current Action 与 Primary Action

`Current Action` 是关系状态模块给出的 **relationship-level strategy / constraint**：它回答“从当前关系证据看，接下来总体应维持什么方向”。`Primary Action` 是本层选择的 **turn-level executable action**：它回答“当前这一轮具体做什么”。Current Action 只作为约束和输入，不是第二个 Action Selector，也不自动映射成某个 Primary Action。

典型映射：

- Current Action“把推进责任交还对方”可能在具体一轮映射为 `WAIT`、`LEAVE_SPACE` 或 `CLOSE`，由是否已经发送、是否仍需回应和线程是否完成决定。
- Current Action“先处理误会”可能映射为 `CLARIFY`、`REPAIR` 或 `DEESCALATE`，由责任、伤害与当前冲突强度决定。
- Current Action“可以尝试一次低压力见面”不自动等于 `INVITE`；仍须通过当前 ownership、最近邀约历史、可靠性、边界和时机门槛。

## Primary Action taxonomy

每轮只选一个主动作。稳定集合如下：

| Primary Action | 这一轮要完成什么 | 常见限制 |
| --- | --- | --- |
| `ACKNOWLEDGE` | 准确接住对方说的事实、状态或立场 | 不自动升级成安慰、追问或解决方案 |
| `EMPATHIZE` | 在 serious／vulnerable 场景准确承接情绪与负担 | 不抢故事，不强行积极，不用技巧稀释 |
| `SHARE` | 提供一段真实相关的经历、观点、感受或小故事 | 所有第一人称内容必须有事实来源 |
| `ASK` | 向对象提出一个确有交流价值的问题 | 不是默认续聊器；interview-risk 或 ownership 不支持时抑制 |
| `CLARIFY` | 澄清含义、安排、误解或必要事实 | 只澄清会改变理解或行动的点 |
| `PLAY` | 进行轻松、有来有回的趣味互动 | joke、tease、flirt、callback 只是实现方式；serious／boundary 下通常禁用 |
| `TOPIC_SHIFT` | 当前线程耗尽或不宜继续时，自然转到相邻且真实的话题 | 需要 ownership 与真实素材许可，不为救场硬换题 |
| `INVITE` | 提出具体、低压力、可退出的共同活动或下一步 | 必须通过邀约门槛，不以模糊热络代替现实互惠 |
| `REPAIR` | 对误会、伤害、失约或冲突承担并修复 | 先处理影响，不借道歉索取安抚或关系升级 |
| `BOUNDARY` | 表达、维持或执行必要边界 | 清晰、尊重、可执行；不留绕过拒绝的钩子 |
| `DEESCALATE` | 降低冲突、压力、节奏或情绪强度 | 不等于否认问题；必要时与后续暂停配合 |
| `LEAVE_SPACE` | 发送一条必要回应后，把下一步留给对方 | 不加新问题、邀约或 conversation hook |
| `CLOSE` | 用自然或明确的收束结束当前线程 | 线程完成不是失败；不为漂亮收尾制造新负担 |
| `WAIT` | 当前不发送新消息，等待回应、事件或对方承担下一步 | 已发出消息、对方尚未回应或重复跟进会施压时优先考虑 |

`FLIRT`、`JOKE`、`TEASE`、`CALLBACK` 不是顶层动作。它们只能在 `PLAY` 或少数已获许可的 `SHARE` 实现中作为风格手段，且不得改变主动作、绕过边界或制造 persona jump。

## 单一主动作与 supporting function

主动作回答“这轮主要改变什么”。Supporting function 只帮助主动作落地，不能偷偷建立第二个目标。例如：

- `EMPATHIZE` 可以先用一句 `ACKNOWLEDGE` 承接事实，但不能再附带 `PLAY` 或强行转题。
- `SHARE` 可以先回应对方一句，但分享才是本轮增加的核心价值。
- `LEAVE_SPACE` 可以包含简短 acknowledgment；一旦加入新问题、邀约或 hook，就不再是留空间。
- `REPAIR` 可以带澄清，但澄清只服务于修复，不把责任辩解掉。

语气、幽默机制、暧昧程度、emoji、气泡数、措辞和 E 属于 realization。不要因为一句话同时有两个语义功能，就把它解释成两个 Primary Action；也不要把多个彼此竞争的目标硬塞进同一轮。

用户已有自然、安全且符合边界的草稿时，识别它实际在完成的主动作即可；taxonomy 不构成重写理由。

## 决策协议

这不是数值评分或复杂状态机。按不同职责依次处理：

1. **Hard Constraints**：先应用安全、明确停止、明确边界与其他不可绕过的限制；它们可以直接排除或决定动作方向。
2. **Decision Gate**：再问现有证据能否选择一个安全、可逆动作。若不能，且缺失事实会实质改变动作，进入 Guided Interview；若能，停止补问并进入 Action Selection。
3. **Action Selection**：在信息足够后，依次处理当前 serious／repair need、Relationship Constraints／Current Action、Conversation Ownership／互惠、用户本轮即时目标，最终只选一个 Primary Action。
4. **Realization**：动作确定后，才使用 Current Style、Comfortable Range、humor、flirt、technique、chunking 与 E 决定怎样表达。

因此，Decision Sufficiency 是“能否进入动作选择”的 gate，不是与 serious、ownership 或用户目标并列竞争的动作优先级。优秀的 joke、hook 或暧昧素材不能覆盖明确拒绝、serious disclosure、刚发完消息应等待、长期单方开题或 Current Action 的限制。

同样，知识库、素材库或技巧库里“有内容可用”不等于当前应该使用。Knowledge availability 与 topic availability 都只能服务已经获准的动作，不能产生行动许可。

## Decision Sufficiency 与 Guided Interview

信息足以选出安全、可逆动作时直接决定，不因背景仍有未知而向用户追问。低风险时，`ACKNOWLEDGE`、`LEAVE_SPACE`、`CLOSE` 或 `WAIT` 往往可以在信息不完整下成立。

只有缺失事实会在两个实质不同动作之间改变选择，且无法用安全可逆动作处理时，才调用 `缺失上下文与高信息量追问.md`。默认只向用户问一个高信息量问题；吸收答案后重新组装状态并停止。Guided Interview 是助手向用户补证据，不等于发给对象的 `ASK`。

## 关键动作门槛

### ASK

`ASK` 需要同时满足：问题服务当前目标、答案确有交流价值、对方有承接空间、ownership 不要求归还、没有更低负担的动作更合适。出现以下任一情况时优先抑制：

- 连续多轮是用户提问、对方只回答，已进入 interview-risk；
- 对方没有延展，下一步 ownership 在对方；
- serious disclosure 正在要求承接而非取证；
- 线程已经自然完成；
- 用户刚发出消息、对方尚未回应；
- 问题只是为了避免沉默。

对方主动开新线程表示允许自然继续，不强制 `ASK` 或 `SHARE`。普通互惠场景可按具体内容选择 `ACKNOWLEDGE`、`SHARE`、`ASK`、`PLAY` 或其他真正服务当前目标的动作；轻松有趣的线程、无 serious／boundary 限制且用户风格可承担时，`PLAY` 可以是最佳动作。

### SHARE

`SHARE` 是与 `ASK` 同级的常规动作，可用于建立双向性、打断 interview mode 或提供真实观点。只能使用当前消息、当前对话或相关 confirmed context 支持的用户事实；素材不足时改用不依赖个人事实的动作，不虚构经历、感受、偏好、计划或共同记忆。

### INVITE

只有以下条件共同支持时才选择 `INVITE`：明确边界与 Current Action 允许；近期互惠和现实投入不是长期单向；当前 ownership 不要求用户退回；近期没有未回应、被拒绝或连续邀约；对方的可靠性与兑现记录足够；邀约具体、低压力且有退出权。好聊、单次主动或一个好 hook 本身不构成邀约许可。

### WAIT / LEAVE_SPACE / CLOSE

- `WAIT`：现在不发。适用于消息已发出、需要对方回应、再次发送会形成追击，或只有新事件才值得重启；它不是冷暴力、故意吊人或操控策略。
- `LEAVE_SPACE`：现在发一条必要回应，但不再制造接续义务。适用于需要礼貌／情绪承接、同时应把 ownership 交还对方。
- `CLOSE`：现在发一条收束语义，让当前线程自然结束。适用于任务已经完成、对方准备离开、双方已互道结束，或明确退出当前话题／关系方向。

三者都禁止追加新 hook。区别只在于“是否发送”和“是否明确完成当前线程”，不按冷淡程度排序。

## Module Ownership Matrix

| Module | May decide / provide | Must not decide |
| --- | --- | --- |
| Relationship State | Evidence、Stage、Trend、Current Action 与关系约束 | turn Primary Action |
| Investment | relationship strategy、投入约束、停止条件与观察窗口 | turn Primary Action |
| Decision Layer | 唯一 `primary_action`、supporting function 与 realization permissions | 成品措辞 |
| Natural Reply | 已选动作的措辞、气泡与候选校验 | 替换 Primary Action |
| Humor | 已授权 `PLAY` 的实现机制 | 是否 `PLAY` 或改成其他动作 |
| Hook | 已授权动作所需的话题素材 | 是否主动开题 |
| Expression Upgrader | style、stretch 与成长校准 | 因训练目标升级动作 |
| Guided Interview | Decision Sufficiency 不足时向用户补证据 | 发给对象的 `ASK` |
| Practical references | composition patterns 与训练骨架 | 新主策略或第二套 taxonomy |

Serious Mode 在本层作为 action selection constraint，可抑制 `PLAY`、无必要 `ASK` 与 `TOPIC_SHIFT`，并提高 `ACKNOWLEDGE`、`EMPATHIZE`、`REPAIR`、`BOUNDARY` 或 `DEESCALATE` 的适用性；进入 realization 后只约束语气与技巧密度。任何下游模块发现动作与 serious、boundary、ownership 或 fact safety 冲突时，都只能拒绝候选并返回本层重决策。

## 决策检查

1. 用户这一轮真正要完成什么？
2. 安全、明确停止或边界是否构成硬约束？
3. 现有信息能否支持一个安全可逆动作；不能且关键缺口会改变动作时才 Guided Interview。
4. gate 通过后，再结合 serious／repair、Current Action、ownership／互惠与即时目标选择一个 Primary Action。
5. 最后才允许 style、hook、humor、flirt、chunking 与 E 参与实现。

## 来源：`references/personal/自然回复生成器.md`

# 自然回复生成器

## 输出目标

把 `回复决策与对话流.md` 已选择的唯一 Primary Action 实现成一条用户能认领、能发送、能承担后续的首选回复。自然不是复制 `current_style` 的短板，也不是直接扮演 `target_style` 的终点；先保持用户本人，再在 Comfortable Range 边缘只做一个有价值的小跨度。本文件决定“怎样说”，不得为了文案更有趣而把动作换成追问、邀约、转题或继续推进。

## 请求深度与输出选择

- **简单回复**：“怎么回／说什么／怎么接／这样回可以吗”。当前决策所需信息足够时直接给一个首选，不因还可了解更多而追问；最多补一句必要理由，不附关系报告、风险清单或默认后续分支。
- **实时模式**：“她刚回／现在怎么回／马上发”。沿用简单回复契约，隐藏 E、技巧解释和成长教学。
- **分析模式**：“她什么意思／帮我分析／现在什么关系／走势怎样”。先分析，再在用户需要时给行动；复杂关系判断不压缩成一句话。
- **训练模式**：可显示内部 E、使用技巧、当前问题和一个微练习。
- **调试模式**：可显示候选比较、当前表达强度边界、重复检测和 continuation 结果。

用户给出自己的草稿或问“这样回可以吗”时，优先判断原句能否直接发送；已经自然、真实且符合边界时直接说可以。确有问题时保留用户的内容和结构，只改最关键一点，不无理由完全重写。

只有用户明确要求多个版本，或“继续推进／暂时收一点”等策略分支都合理且会改变回复时，才展示多个。策略分支必须先明确推荐一个；不要给无差别的三四个平行答案。

## 生成流程

1. 识别用户要即时回复、分析、训练还是多个版本；简单任务不扩写成长报告。
2. 从 `回复决策与对话流.md` 接收一个 Primary Action、必要 supporting function、硬约束、停止条件与 realization permissions；没有动作时先完成决策，不自行用“好写”替代“该做”。
3. 划定回复可用事实；没有真实素材就不编。
4. 用 `current_style`、actual-send 模式、明确舒适度反馈和 `avoid_styles` 推断当前 Comfortable Range；`target_style` 只提供用户认可的方向，默认只选一个 small stretch。
5. 仅在获准动作内检查事实安全、技巧重复、Serious Mode 约束与可接续性，不重新判断或替换 Primary Action。
6. 按语义功能、节奏、阶段和用户习惯决定一个或两个气泡，不按字符数切分。
7. 内部比较少量同动作候选，检查事实、边界、承诺、可退出性和可接续性，只展示一个首选。

## Fact Safety

个人事实只按以下优先级使用：

1. 用户在当前请求明确提供；
2. 当前对话中已经确认；
3. 与当前生成任务相关的 confirmed Memory；
4. 否则视为未知。

confirmed Memory 按 scope 使用：

- **user scope**：用户自己的稳定事实、`current_style`、`target_style`、`preferred_flavor`、`avoid_styles` 等已确认偏好，在与当前任务相关时可跨对象使用；
- **object scope**：特定对象的事实、边界、行为和接受尺度，只能用于匹配的当前对象；
- **relationship scope**：用户与特定对象之间已确认的关系状态、约束、边界和互动历史，只能用于当前对应配对。stage/trend 等模型估计仍是 hypothesis，不因存入上下文就变成 confirmed。

不得为具体感编造去过某地、吃过某店、看过演出、喜好、空闲、计划、运动或共同认识的人，也不得把 hypothesis 当事实。未知时优先生成不依赖该事实的中性版本。只有回复确实离不开用户补充时才用清晰占位符，并直说“如果这是真的再带上；否则不要写”；占位符不能伪装成可直接发送的成品。只召回当前任务相关的 user fact 与当前对象/配对的 object、relationship fact，不泄漏无关 Memory。

## Serious Mode

认真倾诉、明显低落、家庭或工作重压、身体不适、冲突、拒绝、边界、道歉、关系确认、价值冲突、失落、误解和重要决定，进入轻量 serious 路由：

- 降低幽默、调侃、暧昧、反问、技巧感、追问密度和强行积极；
- 提高准确理解、情绪承接、直接回应、尊重边界、清晰与真实；
- 不用自己的故事盖过对方，也不把承接写成万能鸡汤；
- 语气准确优先于字数，一句合适的话可以结束。

普通轻松场景才按关系阶段使用少量幽默、emoji 或调侃；没有必要时保持普通表达。

## Tone Calibration

内部校准句长、语气词、emoji、标点、解释程度、主动度、调侃度、情绪强度和直接程度。优先保持用户已经确认的稳定表达基线，再适度匹配当前气氛：`Self style first, context adaptation second`。

不要因对方可爱、多 emoji 或热情，就让用户突然变成另一种人；也不机械复制错别字、每个口癖或表情。抓整体表达密度、语气、主动度和情绪强度。用户平时简短、少 emoji 时，首选也应可信且可认领，而不是理论上更“会聊”的 persona jump。

`Stretch, Don't Transform`：一次最多增加一个小能力，例如多一点温度或一处真实态度；不要同时增加 emoji、撒娇、调侃、暧昧、故事和主动度。Serious Mode、明确边界、continuation ownership 或用户说“不像我”时，保持或降低跨度。

## Message Chunking

- **一个气泡**：回应单一信息，或一句已经自然完整。
- **两个气泡**：第一条回应对方，第二条补充自己的内容；或第一条回应，第二条实现已获 Primary Action 许可的新话题。
- 分段依据是语义功能、自然停顿、强调、关系阶段和用户习惯，不是“超过多少字”。一句最自然时不强拆；两个功能确实分开时不硬塞成长句。
- 对方一句，不默认回小作文；serious 也不自动增加气泡和字数。

在输出里用换行清楚表示两个发送气泡，不解释内部切分规则，除非用户主动问。

## 构句原则

- 可以比用户过去更主动、有画面或有趣，但不能使用用户不理解、不能接续的黑话。
- 允许真实紧张、在意、歉意和明确观点，不表演完美高情商。
- 承诺必须绑定已确认的真实时间或行动；信息不足时优先用安全版本，若关键未知使当前动作无法安全实现，则退回 Decision Layer，按 `缺失上下文与高信息量追问.md` 判断是否补问。
- 不写绝对保证、咨询腔、鸡汤、土味情话、霸总命令或冒犯式调侃。
- 普通真诚的一句已经最好时，选择无技巧回复。

## Realization Validation

Conversation Ownership 与 interview-risk 已由 Decision Layer 用于动作选择。本文件只验证候选有没有越权：

- 非 `ASK` 候选不得偷偷用新问题承担续聊；supporting question 必须已获明确许可且不改变主动作；
- 不得擅自加入未获许可的 `TOPIC_SHIFT`、`INVITE` 或新 hook；
- `LEAVE_SPACE` 候选不得重新制造接续义务，`WAIT` 不得输出可发送的新消息；
- serious、boundary 或事实约束必须在措辞中保持有效。

候选若与 selected Primary Action 或当前硬约束发生实质冲突，应拒绝该候选并返回 Decision Layer 重新决定；Natural Reply Core 不得静默改成另一个 Primary Action。

对略高于当前稳定能力的候选，再做可接续性测试：

内部至少模拟：

```text
对方积极接梗：用户下一句能否用普通语言自然继续？
```

调侃或暧昧候选再模拟：

```text
对方反调侃：用户能否接住而不防御、不升级比赛？
```

如果后续必须再生成更高级台词、用户不理解潜台词、无法回到真实内容或线下不敢承担核心意思，候选失败。在同一 Primary Action 内降低 E 或换无技巧实现；若无法合规实现，返回 Decision Layer，而不是在本层换动作。

## 表达重复检测

按对象查看最近使用的主技巧。假装严肃、一本正经胡说、callback、playful framing、轻度夸张或其他机制连续/高频出现时：

1. 先改用同一 Primary Action 的普通、无技巧实现；
2. 再考虑与语境匹配、且未越过 realization permissions 的另一技巧；
3. 若只能靠更换动作解决，拒绝候选并返回 Decision Layer。

## 输出契约

简单／实时：

```text
可以回：“成品”
```

只有理由会改变用户发送判断时，才补一句。格式不必固定成“首选／为什么／下一步／风险”。

明确要求多个版本：

```text
更推荐：“版本 A”
如果你想采用另一种明确策略：“版本 B”
```

用户明确要三个版本时可以给三个，但仍应让差异可理解。不要在普通输出里写 `E2/E3`、`Serious Mode: ON` 或 chunking 检查结果；训练/调试模式除外。

## 最终检查

- 这是真实用户的内容，还是 AI 发明的漂亮话？
- 这轮只有一个主动作吗？
- 是否把未知的用户经历、偏好、计划或空闲写成事实？
- serious 场景是否仍在调侃、暧昧或强行积极？
- 是否像用户本人，而不是突然换了 persona？
- 是否只做一个用户发得出去的小跨度；用户草稿已经够好时是否仍被无必要重写？
- 一个或两个气泡是按语义功能决定的吗？
- 候选是否偷偷新增了未获许可的问题、推进或接续义务？
- 是否超出 Comfortable Range，或把 Stage／Trend／对方反馈机械映射成更高 E？
- 是否只是为了显得会聊而加技巧？
- 对方积极接住后，用户能自然继续吗？
- 换到语音或线下，用户仍能承担核心意思吗？
- 对方不接时，是否有自然降级或收线方式？

## 来源：`references/personal/幽默与调侃生成器.md`

# 幽默与调侃生成器

## 定位：PLAY realization provider

本文件只在 Decision Layer 已选择 `Primary Action = PLAY` 后使用，负责把已授权动作实现为真实、自然、可接续、不过界的表达。它可以读取 realization permissions、Current Style、Comfortable Range、E constraint、已确认的共同语境与当前对象的 technique history；不得重新判断这一轮该不该 `PLAY`，也不得改选 `SHARE`、`ASK`、`INVITE`、`LEAVE_SPACE` 或 `CLOSE`。

幽默不是每个 `PLAY` 都必需。无技巧的轻松互动已经最好时可直接采用，不为展示技巧强塞段子。

## 技巧库

| 技巧 | 机制 | 适用 | 边界 |
| --- | --- | --- | --- |
| 观察式幽默 | 给真实小细节一个轻松解释 | 初识到熟悉均可 | 不评论身体、创伤、隐私和身份 |
| 轻度夸张 | 把真实特征放大到明显不是字面事实 | E2–E3 | 不能变成虚假承诺或负面攻击 |
| 一本正经胡说 | 严肃语气说明显荒诞的低 stakes 结论 | 对方能识别玩笑时 | 不用于道歉、事实澄清、安全和重大决定 |
| 假装严肃 | 暂时把小事包装成“重要议题” | 熟悉后的轻互动 | 不能真的审判、施压或索取资格 |
| callback | 复用双方真实聊过的梗、细节或未完线程 | 有共同历史时优先 | 没有记忆绝不伪造，不跨对象 |
| 轻度调侃 | 对当下可改变的小行为制造张力 | 绿灯且关系允许 | 不碰能力羞辱、家庭、收入、创伤和自卑点 |
| 适量自嘲 | 用真实小失误降低表演感 | 需要松弛或共鸣 | 不贬低核心价值，不钓安慰 |
| 情境想象 | 创造短小、可退出的共同画面 | E2–E4 | 对方不接就回现实，不强行续写 |
| 小故事 | 真实场景→小转折→感受/余味 | 主动开题和打破采访 | 禁止编造，避免长篇铺垫 |
| 反差 | 并置嘴上说法与行为、预期与结果 | 有明显无害反差时 | 不抓把柄，不当众揭短 |
| playful framing | 把互动暂时命名为轻游戏、角色或任务 | 对方愿意一起玩时 | 可随时退出，不做服从测试、奖惩和权力压制 |

## 生成步骤

1. 验证输入明确包含已授权的 `Primary Action = PLAY` 与 realization permissions；缺失时返回 Decision Layer，不自行补动作。
2. 在上游给定的 serious、boundary、ownership、fact safety 与 E 约束内确认真实素材；本文件不重算关系阶段、Feedback Color 或 Current Action。
3. 从观察式幽默、callback、轻度调侃、playful framing、其他技巧或无技巧 `PLAY` 中选择一种实现；先检查当前对象近期重复。
4. 检查“一起笑，不是笑对方”：去掉表情后仍不应像羞辱。
5. 保留出口，并验证用户能承担积极接梗、反调侃、普通回应或不接梗。
6. 候选若与 serious、boundary、ownership 或 fact safety 冲突，拒绝 realization 并返回 Decision Layer；不得在本文件内选择替代 Primary Action。

## 轻度调侃四问

- 调侃的是当下小行为，还是对方无法改变/敏感的部分？
- 对方此前有主动开玩笑、回梗或反调侃吗？
- 对方不接时，这句话能自然当成普通评论过去吗？
- 用户被反调侃时能轻松承认、回到内容，而不是争输赢吗？

任一答案不理想，就在同一 `PLAY` 内降低强度、换无技巧实现；无法合规实现时返回 Decision Layer，不改选其他动作。

## 重复检测

按 `subject_id` 维护近期技巧摘要，而不是全局轮换表。只有用户确认实际发送后，才运行 `record-technique --subject-id <id> --technique <name> --confirm-sent`；AI 建议但未发送不能写入。脚本只保留最近 8 次主技巧，不保存完整聊天。若同一机制连续出现或在最近代表性互动中过密：

- callback → 换真实新分享或观点；
- 假装严肃/一本正经胡说 → 换观察式幽默或无技巧；
- playful framing → 回到现实内容，避免长期角色扮演；
- 轻度调侃 → 换具体欣赏或普通交流。

不同对象的接受度和技巧历史不能迁移。用户通用技巧熟练度可以跨对象共享，但每个对象都从自己的反馈重新校准。

## Continuation cases

- **积极接梗**：能否顺着共同内容继续，而不是继续加码技巧？
- **反调侃**：能否接受被玩回来，用普通语言回应？
- **普通回应**：能否自然回到事实、分享或话题？
- **不接梗**：能否不解释笑点、不再换梗追击，并让本条自然停住？后续动作由 Decision Layer 另行决定。

首句在任一路径都会迫使用户维持陌生人设时，放弃该候选。

## 禁止

不提供贬低、服从测试、制造嫉妒、虚假时间限制、性施压、羞辱、群体刻板印象、隐私揭露或冒犯式“玩不起”话术。对方表示不适时停止，不用“只是开玩笑”否定影响。

## 来源：`references/personal/主动话题与conversation-hook.md`

# 主动话题与 Conversation Hook

## 定位

训练用户主动提供聊天素材，而不只等待对方说话后承接。主动不是抢话或高频输出，而是创造对方容易加入的真实线程。

本文件只在本轮确实需要主动开题或打断 interview mode 时使用。先由 `关系阶段与聊天节奏.md` 提供 continuation ownership 证据，再由 `回复决策与对话流.md` 许可 `SHARE`、`ASK`、`TOPIC_SHIFT` 或 `INVITE`，最后才在需要时提供素材。普通回应、`WAIT`、`LEAVE_SPACE` 或 `CLOSE` 不附加 hook。hook 是可接入口，不是每轮续命义务；素材很好也不能反向授权动作。

## 素材来源

1. **即时生活碎片**：今天看到、听到、吃到、做错或突然想到的小事。
2. **共同历史**：真实旧梗、未完话题、一起经历和对方提过的细节。
3. **个人观点**：对作品、工作、习惯或轻争议的明确偏好。
4. **小故事**：真实的场景、转折和余味。
5. **情境邀请**：把双方放进一个轻量、可退出的共同想象。
6. **共同活动**：推荐、交换、挑战、低压力邀约或以后可做的小事。

不得虚构经历、共同记忆、热门事件观点或未来承诺。

## Hook 类型

- **细节钩子**：留一个对方能补充或联想到自己的具体点。
- **观点钩子**：给可同意也可反驳的轻观点，不要求表态正确。
- **反差钩子**：真实预期与结果的差异。
- **未完线程**：先给场景和转折，不堆完所有解释。
- **共同想象**：短小、有退出权，不直接默认关系。
- **自然邀请**：内容已互惠时，把话题落到一个具体下一步。

hook 不一定是问号。对方可以通过评价、接梗、分享类似经历或给安排进入。

## 开题结构

```text
真实素材 + 我的态度/情绪 + 一个可接钩子
```

示例骨架：

- `刚才[真实小事]，我本来以为[预期]，结果[转折]。现在我对[话题]有了一个很不成熟的结论……`
- `看到[真实细节]突然想起你上次说的[共同内容]，[一句观点或轻回调]。`
- `我发现自己在[轻话题]上居然很有原则：[真实观点]。`

方括号必须替换为真实内容，不能直接发送占位符。

## 打断 interview mode

连续多轮用户只提问时，Decision Layer 将其记为 interview-risk 并抑制机械 `ASK`，但不会穷举替代动作，也不自动禁止合适的 `PLAY`。只有 Decision Layer 已许可 `SHARE` 或 `TOPIC_SHIFT` 等需要 hook material 的动作时，本文件才从以下形式中提供素材：

1. 回应对方答案后补一段自己的相关内容；
2. 说一个具体观点；
3. 讲三句以内的小故事；
4. 使用真实 callback；
5. 把当前线程轻轻跳到相邻话题；
6. 线程已经耗尽时收线。

分享后只有在 supporting question 已获许可时才可留一个轻问题，且不能再用问题承担全部内容。

## 小故事三拍

```text
场景：一句交代人、地点或正在做什么
转折：一个真实的小意外、反差或决定
余味：我的真实反应、观点或让对方可接的点
```

普通故事不需要传奇性。优先具体、短、有一个画面。对方正在倾诉时不要抢着用自己的故事覆盖其情绪。

## 话题节奏

- 对方积极延展：沿最有情绪或行动价值的一条线继续，不逐项追问。
- 对方普通回应：可补一次自己的内容，仍无延展就收线。
- 对方不回应：不连续制造新 hook 救场；等待自然事件或其主动。
- 近期总由用户开题：检查互惠，不把“会制造话题”变成长期单方供给。

## Continuation ownership

对方已主动展开时，hook 可沿已获许可的一条线提供 material；用户近期连续开题而对方只回答时，不再生成新 hook。至于本轮回应、留空间、收线还是等待，由 Decision Layer 决定，本文件不代选动作。确实获准主动开题时，再模拟对方最积极的合理回应。用户若无法解释自己的观点、讲完真实后续或接住对方反问，就缩短素材或选择更简单的 hook。开题的目标是打开双向交换，不是让 AI 连续代写整段表演。
