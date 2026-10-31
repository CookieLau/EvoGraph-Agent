# data/ — 第二份泛化测试场景（EV 电池供应链）

用途：说明进化后的 prompt 不是对主 payload（`dirty_payload.py`，半导体场景）过拟合。演示结尾可以把进化后的 prompt 生成的脚本拿来跑这份数据。

| 文件 | 内容 |
|---|---|
| `scenario_ev_battery.json` | 15 个节点、18 条边。格式与主 payload 相同：`nodes[id,name,tier,exposure_raw,status]`，`edges[src,dst,rel,weight,note]` |
| `scenario_ev_battery_truth.json` | 标准答案，格式同 CONTRACT.md 的 `GROUND_TRUTH`，另加 `expected_contagion`：22 条 Tier4→持仓路径，3–4 跳，按传染系数从高到低排列 |
| `_verify.py` | 参考解析器加自检。`.venv/bin/python data/_verify.py`；改了 truth 的边之后加 `--write-contagion` 重算 |

## 新增的脏数据形态（与主数据同类，但写法不同）
- 名字里的单引号：`Albemarle's Kings Mountain`、`Sumitomo Metal Mining 'NCA' Line`；note 里也有单引号
- 金额：`¥3.1B`、`¥850M`（JPY）、`2,400,000 USD`、`$0.75M`、`1.2K`（没有币种）、`" 640,000 USD "`（首尾有空格）、`2.2M usd`（币种小写）、`null`
- 权重：`CRITICAL_0.97`、**`HIGH_0.8`（新前缀）**、`" 0.66 "`（首尾有空格）、`"0.5"`（数字写成字符串）

## 标准化规则（truth 按这些规则生成）
- 金额保持原币种，不做汇率换算。`$`→USD，`€`→EUR，`¥`→JPY；币种代码不区分大小写。K/M/B 分别乘 1e3/1e6/1e9。
- `null` 或空字符串 → `exposure 0.0, currency null`；**没有币种标记 → `currency null`**（例如 `1.2K`）
- 权重：先去掉首尾空格，再去掉任意 `LABEL_` 前缀（正则 `^[A-Za-z]+_`），然后 `float()`
- 名字原样保留，单引号不能丢

## 关键传染路径（用来讲故事）
- 刚果钴矿断供 → 华友钴业 → EcoPro → LGES → **TSLA**：系数 0.6116，约 $9.30M 受影响
- 智利锂盐湖 → 赣锋 → EcoPro → LGES → **TSLA**：系数 0.5659，约 $8.60M
- 刚果钴矿 → 华友钴业 → 住友金属矿山 → **Panasonic**：系数 0.5173，约 ¥1.60B
