# Memory 生命周期

## 时间规范

`created_at`、`updated_at`、`occurred_at`、`expires_at` 统一使用带时区的 ISO 8601。脚本通过 `scripts/date_utils.py` 校验并归一到 UTC；无时区或不可解析的输入被拒绝。

- `created_at`：记录首次创建时间；
- `updated_at`：记录内容或生命周期状态最近更新时间；
- `occurred_at`：事件真实发生时间，时间线以它为准；
- `expires_at`：hypothesis 的 active 有效期终点。

补录旧事件只更新本地写入时间，不会把旧事排成最新发生事件。

## 各类 Memory 生命周期

| 类型 | 生命周期 | 自动行为 |
| --- | --- | --- |
| user/object/relationship confirmed | 用户确认前不写稳定字段 | 不设置 TTL；字段更新或用户明确删除 |
| event landmark | 长期节点 | 不进入普通 FIFO 淘汰 |
| event normal | 一般关键事件 | 超过对象 event 上限后可按发生时间淘汰 |
| event temporary | 短期观察点 | 超限时优先淘汰 |
| hypothesis | active → stale / superseded | 到期转 stale；同字段新判断使旧记录 superseded；不因 TTL 删除 |

## Hypothesis TTL

| field | TTL |
| --- | --- |
| `stage_estimate` | 30 天 |
| `trend_estimate` | 14 天 |
| `humor_receptivity` / `humor_acceptance` | 30 天 |
| `style_update` / legacy `style_update_suggestion` | 30 天 |

`context` 默认只召回 active hypothesis；stale 和 superseded 不进入即时建议。`show` 仍展示全部生命周期状态，便于审计和纠错。TTL 到期不是“判断错误”，只表示证据需要重新校准。

## 状态含义

- `active`：仍可作为当前模型判断参与上下文；
- `stale`：超过 TTL，保留但默认不召回；
- `superseded`：同字段已有更新判断，旧记录保留用于审计。

任何稳定画像更新仍需用户确认。对象级 hypothesis 不得迁移给其他对象。
