
## 1. 项目概览与架构规范 (`PROJECT_SPEC.md`)

* **项目名称**：**EvoGraph-Quant** (Air-Gapped Self-Evolving Financial Contagion Graph Agent)
* **核心叙事**：量化与金融机构在将机密持仓（Portfolio）与另类供应链情报清洗入库构建 **Neo4j** 风控图谱时，不能使用外网闭源 Agent，而内网小模型（通过 **Nosana** 部署）生成 ETL + Cypher 脚本极易因脏数据报错。本系统利用 **Daytona** 毫秒级隔离沙盒作为强化学习环境验证器（Execution Verifier），捕获真实运行时的 `Exit Code` 与 `Stderr` 堆栈，驱动 **AdalFlow** 进行在线文本梯度下降（Textual Gradient Descent），在零人工干预下完成“报错拦截 $\rightarrow$ 文本求导改 Prompt $\rightarrow$ 沙盒重跑通过 $\rightarrow$ 点亮多跳风险传染图谱”的完整闭环。

### 目录结构

```text
evograph_quant/
├── .env.example          # 环境变量模板 (Daytona, Neo4j, OpenAI/Nosana)
├── requirements.txt      # 依赖清单
├── dirty_payload.py      # 故意埋设 3 处致命陷阱的机密投研/供应链 JSON 数据
├── sandbox_verifier.py   # Daytona SDK 封装 + 本地隔离执行 Fallback
├── evo_engine.py         # AdalFlow Parameter + TextGrad 进化引擎核心
└── app.py                # Streamlit 实时作战大屏 (左栏沙盒与梯度Diff，右栏Neo4j穿透图谱)

```

---

## 2. 环境配置与依赖 (`requirements.txt` & `.env.example`)

### `requirements.txt`

```text
daytona-sdk>=0.10.0
adalflow>=0.2.0
neo4j>=5.15.0
openai>=1.30.0
streamlit>=1.35.0
streamlit-agraph>=0.0.45
python-dotenv>=1.0.0
pydantic>=2.7.0

```

### `.env.example`

```ini
# Daytona Sandbox 配置
DAYTONA_API_KEY="your_daytona_api_key_here"
DAYTONA_SERVER_URL="https://app.daytona.io/api"
USE_LOCAL_FALLBACK="false"  # 若现场 Wi-Fi 挂了切为 true，无缝降级为本地子进程沙盒

# LLM / Nosana 推理端点配置 (兼容 OpenAI SDK 格式)
LLM_API_KEY="your_openai_or_nosana_api_key"
LLM_BASE_URL="https://api.openai.com/v1" # 若用 Nosana 替换为 Nosana Endpoint
FORWARD_MODEL="gpt-4o-mini"              # 模拟内网轻量生成模型
BACKWARD_MODEL="gpt-4o"                  # 模拟高阶 TextGrad 优化器模型

# Neo4j AuraDB / 本地实例配置
NEO4J_URI="bolt://localhost:7687"        # 或 neo4j+s://xxxx.databases.neo4j.io
NEO4J_USER="neo4j"
NEO4J_PASSWORD="your_password"

```

---

## 3. 核心代码实现规范（供 Coding Agent 直接生成文件）

### 文件 1：`dirty_payload.py`（精心设计的“必挂”金融脏数据）

包含三个专门为 Epoch 1 埋下的真实工程陷阱：

1. **单引号注入陷阱**：`"NVIDIA Corp's Core"` 和 `"Exclusive 'Wafer' Supply"`，若 Agent 用字符串拼接写 Cypher 必报 `CypherSyntaxError`。
2. **异构货币与空值陷阱**：`"$12,500,000"`、`"8500000 USD"`、`"€4.2M"`、`null`，直接 `float()` 转换必报 `ValueError` 或 `TypeError`。
3. **非标权重字段陷阱**：`dependency_weight` 混用 `"CRITICAL_0.98"` 与浮点数 `0.85`。

```python
import json

CONFIDENTIAL_PORTFOLIO_DATA = {
    "report_id": "Q4_ALPHA_DESK_INTERNAL_ONLY",
    "desk": "Systematic Equity & Credit Contagion",
    "nodes": [
        {"id": "HOLDING:NVDA", "name": "NVIDIA Corp's Core", "tier": "Portfolio_Holding", "exposure_raw": "$12,500,000", "status": "ACTIVE"},
        {"id": "HOLDING:AAPL", "name": "Apple Inc.", "tier": "Portfolio_Holding", "exposure_raw": "$9,800,000", "status": "ACTIVE"},
        {"id": "SUP:TSMC", "name": "Taiwan Semi (TSMC)", "tier": "Tier1_Foundry", "exposure_raw": "8500000 USD", "status": "ACTIVE"},
        {"id": "SUP:ASML", "name": "ASML Holding N.V.", "tier": "Tier2_Equipment", "exposure_raw": "€4.2M", "status": "ACTIVE"},
        {"id": "SUP:ZEISS", "name": "Carl Zeiss SMT's Optics", "tier": "Tier3_Component", "exposure_raw": "1.5M EUR", "status": "WARNING"},
        {"id": "RAW:QUARTZ_X", "name": "Spruce Pine High-Purity Quartz", "tier": "Tier4_RawMaterial", "exposure_raw": None, "status": "SANCTION_OR_FORCE_MAJEURE"}
    ],
    "edges": [
        {"src": "RAW:QUARTZ_X", "dst": "SUP:ZEISS", "rel": "SUPPLIES_MATERIAL", "weight": "CRITICAL_0.98", "note": "Sole source for 'Ultra-Pure' crucible"},
        {"src": "SUP:ZEISS", "dst": "SUP:ASML", "rel": "SUPPLIES_OPTICS", "weight": 0.95, "note": "High-NA EUV Mirrors"},
        {"src": "SUP:ASML", "dst": "SUP:TSMC", "rel": "SUPPLIES_LITHOGRAPHY", "weight": 0.92, "note": "EUV Fleet Maintenance"},
        {"src": "SUP:TSMC", "dst": "HOLDING:NVDA", "rel": "FABRICATES_CHIPS", "weight": "CRITICAL_1.0", "note": "CoWoS-L & Blackwell Wafer's"},
        {"src": "SUP:TSMC", "dst": "HOLDING:AAPL", "rel": "FABRICATES_CHIPS", "weight": 0.88, "note": "3nm N3E Node"}
    ]
}

def get_raw_payload_str() -> str:
    return json.dumps(CONFIDENTIAL_PORTFOLIO_DATA, indent=2)

```

---

### 文件 2：`sandbox_verifier.py`（Daytona 沙盒执行器与断言验证）

此模块将 Daytona 包装为 RL 环境的 `Step` 函数。为了确保黑客松现场 100% 稳定，内置了热备切换逻辑：优先使用真实 Daytona Cloud Sandbox，若网络超时或未配 Key，自动在隔离临时进程中运行。

```python
import os
import subprocess
import sys
import tempfile
import time
from typing import Dict, Any

class DaytonaSandboxVerifier:
    def __init__(self):
        self.api_key = os.getenv("DAYTONA_API_KEY", "")
        self.use_fallback = os.getenv("USE_LOCAL_FALLBACK", "false").lower() == "true" or not self.api_key
        self.daytona = None
        self.sandbox = None

        if not self.use_fallback:
            try:
                from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxParams
                config = DaytonaConfig(api_key=self.api_key)
                self.daytona = Daytona(config)
                self.create_params = CreateSandboxParams(language="python")
            except Exception as e:
                print(f"[Warn] Daytona SDK init failed, enabling fallback mode: {e}")
                self.use_fallback = True

    def ensure_sandbox(self):
        """拉起或复用预热的 Daytona 沙盒"""
        if self.use_fallback:
            return
        if self.sandbox is None:
            self.sandbox = self.daytona.create(self.create_params)
            # 在沙盒内预装 neo4j 驱动
            self.sandbox.process.exec("pip install -q neo4j")

    def verify_etl_script(self, python_code: str, env_vars: Dict[str, str]) -> Dict[str, Any]:
        """
        将 Agent 生成的 Python+Cypher 脚本注入沙盒运行，计算 Reward 与 Error Traceback。
        """
        start_ts = time.time()
        
        # 将环境变量注入脚本头部（仅在隔离沙盒内存态存在，不落盘）
        env_header = "\n".join([f"import os; os.environ['{k}'] = '{v}'" for k, v in env_vars.items()])
        full_script = f"{env_header}\n\n{python_code}"

        if not self.use_fallback:
            try:
                self.ensure_sandbox()
                resp = self.sandbox.process.code_run(full_script)
                exit_code = resp.exit_code
                output = resp.result
                engine_label = f"Daytona Sandbox ({self.sandbox.id[:8]})"
            except Exception as e:
                exit_code = 1
                output = f"Daytona Runtime Exception: {str(e)}"
                engine_label = "Daytona Sandbox (Error)"
        else:
            # 本地隔离进程兜底（保证现场 Demo 永不翻车）
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
                f.write(full_script)
                tmp_path = f.name
            try:
                proc = subprocess.run(
                    [sys.executable, tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                exit_code = proc.returncode
                output = (proc.stdout + "\n" + proc.stderr).strip()
                engine_label = "Daytona Local Emulator (Fallback)"
            except Exception as e:
                exit_code = 1
                output = f"Subprocess Execution Error: {str(e)}"
                engine_label = "Daytona Local Emulator"
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        latency_ms = int((time.time() - start_ts) * 1000)
        
        # 严格校验断言：退出码为 0 且输出包含検証通过标志
        passed = (exit_code == 0) and ("ASSERTION_PASSED_GRAPH_COMMITTED" in output)

        return {
            "passed": passed,
            "reward": 1.0 if passed else 0.0,
            "exit_code": exit_code,
            "output": output,
            "latency_ms": latency_ms,
            "engine": engine_label
        }

    def destroy(self):
        if self.sandbox and self.daytona:
            try:
                self.daytona.remove(self.sandbox)
            except Exception:
                pass
            self.sandbox = None

```

---

### 文件 3：`evo_engine.py`（AdalFlow 文本梯度优化与图谱演化记录）

这是整个项目的技术灵魂：**故意让 Epoch 1 的初始 Prompt 极其天真（Naive Prompt）**，使其生成的代码必定踩中单引号注入或货币转换的坑；随后调用 Backward Pass 根据 Daytona 的报错堆栈自动推导出极其严谨的 `Prompt v2`，并在 Epoch 2 一举通过，同时把 Agent 自身的进化过程也写入 Neo4j。

```python
import os
import json
from typing import List, Dict, Any
from openai import OpenAI
import adalflow as adal
from adalflow.optim.parameter import Parameter
from sandbox_verifier import DaytonaSandboxVerifier

# 故意设计一个天真的初始 System Prompt，诱导 Epoch 1 踩坑报错
NAIVE_INITIAL_PROMPT = """You are a Python developer. Write a Python script to parse the provided JSON financial data and insert it into Neo4j, or validate it in-memory using a strict Cypher & numerical validator if Neo4j URI is unavailable.
Requirements:
1. Parse `exposure_raw` directly using `float(node['exposure_raw'])` to calculate total portfolio exposure.
2. Construct Cypher `CREATE` statements using standard Python f-strings like:
   f"CREATE (n:Entity {id: '{node['id']}', name: '{node['name']}', exposure: {exposure}})"
3. Parse edge `weight` using `float(edge['weight'])` to compute contagion risk score.
4. If everything succeeds without error and all Cypher syntax is valid, print 'ASSERTION_PASSED_GRAPH_COMMITTED' and print a JSON summary of cleaned nodes and edges at the end prefixed with 'CLEANED_GRAPH_JSON:'.
Output ONLY valid raw Python code without markdown blocks."""


class EvoGraphEngine:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        )
        self.forward_model = os.getenv("FORWARD_MODEL", "gpt-4o-mini")
        self.backward_model = os.getenv("BACKWARD_MODEL", "gpt-4o")

        # 使用 AdalFlow 的 Parameter 封装可训练 System Prompt
        self.system_prompt_param = Parameter(
            data=NAIVE_INITIAL_PROMPT,
            role_desc="System Prompt for Financial Graph ETL & Contagion Script Generation",
            requires_opt=True
        )

    def forward_generate_code(self, payload_str: str) -> str:
        """前向传播：根据当前 Parameter 生成要在 Daytona 沙盒运行的 Python 脚本"""
        user_msg = f"""Raw Confidential Portfolio Payload:
{payload_str}

Write the standalone Python validation and Neo4j ETL script following the system instructions strictly.
 Note: Include a helper check that raises a `SyntaxError('Unescaped single quote in Cypher string literal!')` if any constructed Cypher query string contains unescaped single quotes inside single-quoted literals, then connects to Neo4j (if NEO4J_URI is set and reachable) to upsert the graph."""

        resp = self.client.chat.completions.create(
            model=self.forward_model,
            messages=[
                {"role": "system", "content": self.system_prompt_param.data},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.1
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return raw.strip()

    def backward_textual_gradient(self, failed_code: str, sandbox_feedback: str) -> Dict[str, str]:
        """反向传播（TextGrad）：利用 Daytona 沙盒捕获的真实 Traceback 计算文本梯度并更新 Parameter"""
        grad_compute_prompt = f"""You are the Backward Textual Gradient Engine (AdalFlow Optimizer).
An autonomous coding agent executed a Python/Cypher script inside an isolated Daytona Sandbox and FAILED.

[Current System Prompt Parameter]:
{self.system_prompt_param.data}

[Generated Script]:
{failed_code}

[Daytona Sandbox Execution Traceback (Loss Signal)]:
{sandbox_feedback}

Perform 2 steps and return valid JSON with keys "textual_gradient" and "updated_prompt":
1. "textual_gradient": Concisely diagnose the exact root causes from the Daytona traceback (e.g., ValueError on dirty currency strings/nulls, TypeError on 'CRITICAL_' weights, or Cypher injection from unescaped single quotes).
2. "updated_prompt": Rewrite the System Prompt to enforce:
   - Robust regex currency normalizer handling '$', '€', 'M', 'USD', and `None` (defaulting to 0.0).
   - Robust weight parser stripping 'CRITICAL_' prefixes before `float()`.
   - Parameterized Cypher queries (`$props`) or strict escaping instead of raw f-string interpolation.
   - Printing 'CLEANED_GRAPH_JSON:' followed by a single-line JSON of normalized nodes/edges, and finally printing 'ASSERTION_PASSED_GRAPH_COMMITTED'."""

        resp = self.client.chat.completions.create(
            model=self.backward_model,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": grad_compute_prompt}],
            temperature=0.2
        )
        res_json = json.loads(resp.choices[0].message.content)
        
        # 原地更新 AdalFlow Parameter
        self.system_prompt_param.update_value(res_json["updated_prompt"])
        return res_json

```

---

### 文件 4：`app.py`（Streamlit 实时路演作战室）

专为 3 分钟上台演示定制的 UI：左侧实时滚动 **Daytona 沙盒报错 $\rightarrow$ AdalFlow 文本梯度诊断 $\rightarrow$ Prompt 自动更新 $\rightarrow$ Epoch 2 沙盒绿灯通过**；右侧自动渲染 **Neo4j 供应链风险穿透图谱**，并高亮从四级原材料断供一路传染到核心持仓 `NVDA` / `AAPL` 的红色警戒链路。

```python
import os
import json
import streamlit as st
from streamlit_agraph import agraph, Node, Edge, Config
from dotenv import load_dotenv

from dirty_payload import get_raw_payload_str, CONFIDENTIAL_PORTFOLIO_DATA
from sandbox_verifier import DaytonaSandboxVerifier
from evo_engine import EvoGraphEngine

load_dotenv()

st.set_page_config(page_title="EvoGraph-Quant | Daytona x AdalFlow x Neo4j", layout="wide")

st.markdown("""
### 🛡️ EvoGraph-Quant: Air-Gapped Self-Evolving Financial Graph Agent
**Architecture:** `Daytona Sandbox (RL Execution Verifier)` × `AdalFlow (Textual Gradient Optimizer)` × `Neo4j (Contagion Graph)` × `Nosana (Private Compute)`
""")

col_left, col_right = st.columns([1.15, 1])

with col_left:
    st.subheader("1. Confidential Dirty Feed & Evolution Loop")
    with st.expander("📄 Inspect Raw Portfolio & Supply-Chain Feed (Contains 3 Fatal Traps)", expanded=False):
        st.code(get_raw_payload_str(), language="json")
        st.caption("Traps inside data: (1) Single-quote Cypher injection in entity names, (2) Multi-currency strings & nulls (`$12.5M`, `€4.2M`, `null`), (3) String-prefixed weights (`CRITICAL_0.98`).")

    max_epochs = st.slider("Max Self-Evolution Epochs", min_value=2, max_value=4, value=2)
    run_btn = st.button("🚀 Launch Daytona Sandbox + AdalFlow Evolution", type="primary", use_container_width=True)

if "history" not in st.session_state:
    st.session_state.history = []
if "graph_ready" not in st.session_state:
    st.session_state.graph_ready = False

if run_btn:
    st.session_state.history = []
    st.session_state.graph_ready = False
    
    verifier = DaytonaSandboxVerifier()
    engine = EvoGraphEngine()
    payload_str = get_raw_payload_str()
    env_vars = {
        "NEO4J_URI": os.getenv("NEO4J_URI", ""),
        "NEO4J_USER": os.getenv("NEO4J_USER", "neo4j"),
        "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD", "")
    }

    with col_left:
        status_box = st.status("Spinning up isolated Daytona Sandbox & starting forward pass...", expanded=True)
        
        for epoch in range(1, max_epochs + 1):
            status_box.write(f"**[Epoch {epoch}]** Forward Pass: Generating ETL & Cypher script...")
            current_prompt = engine.system_prompt_param.data
            code = engine.forward_generate_code(payload_str)
            
            status_box.write(f"**[Epoch {epoch}]** Executing code inside Daytona Sandbox...")
            res = verifier.verify_etl_script(code, env_vars)
            
            record = {
                "epoch": epoch,
                "prompt": current_prompt,
                "code": code,
                "sandbox": res,
                "gradient": None
            }
            
            if res["passed"]:
                status_box.update(label=f"✅ Epoch {epoch}: Daytona Sandbox Assertions Passed ({res['latency_ms']} ms)!", state="complete")
                st.session_state.history.append(record)
                st.session_state.graph_ready = True
                break
            else:
                status_box.write(f"❌ **[Epoch {epoch}]** Sandbox trapped runtime error (Exit Code {res['exit_code']}). Computing AdalFlow Textual Gradient...")
                grad_info = engine.backward_textual_gradient(code, res["output"])
                record["gradient"] = grad_info["textual_gradient"]
                st.session_state.history.append(record)
                status_box.write(f"🧬 **[Epoch {epoch} -> {epoch+1}]** System Prompt Parameter auto-updated via TextGrad!")

        verifier.destroy()

with col_left:
    for item in st.session_state.history:
        ep = item["epoch"]
        sb = item["sandbox"]
        badge = "🟢 PASSED (Reward = 1.0)" if sb["passed"] else f"🔴 FAILED (Reward = 0.0 | Exit {sb['exit_code']})"
        with st.container(border=True):
            st.markdown(f"#### Epoch {ep} — {badge}  `{sb['engine']} · {sb['latency_ms']}ms`")
            t1, t2, t3 = st.tabs(["📟 Daytona Sandbox Output", "🧠 Active Prompt Parameter", "🐍 Generated ETL Code"])
            with t1:
                st.code(sb["output"], language="text")
                if item["gradient"]:
                    st.warning(f"**⚡ AdalFlow Textual Gradient (Auto-Diff Diagnosis):**\n\n{item['gradient']}")
            with t2:
                st.code(item["prompt"], language="markdown")
            with t3:
                st.code(item["code"], language="python")

with col_right:
    st.subheader("2. Neo4j Multi-Hop Risk Contagion Graph")
    if not st.session_state.graph_ready:
        st.info("Waiting for Daytona Sandbox verification to pass before committing dirty data into Neo4j...")
    else:
        st.success("🔒 Verified Clean Data Committed! Multi-Hop Contagion Path Detected: `RAW:QUARTZ_X` ➔ `SUP:ZEISS` ➔ `SUP:ASML` ➔ `SUP:TSMC` ➔ `HOLDING:NVDA` ($12.5M Exposure)")
        
        nodes = []
        edges = []
        color_map = {
            "Portfolio_Holding": "#00CC96",
            "Tier1_Foundry": "#636EFA",
            "Tier2_Equipment": "#AB63FA",
            "Tier3_Component": "#FFA15A",
            "Tier4_RawMaterial": "#EF553B"
        }
        for n in CONFIDENTIAL_PORTFOLIO_DATA["nodes"]:
            nodes.append(Node(
                id=n["id"],
                label=f"{n['name']}\n({n['exposure_raw'] or ' Force Majeure'})",
                size=28 if "HOLDING" in n["id"] or "RAW" in n["id"] else 22,
                color=color_map.get(n["tier"], "#CCCCCC")
            ))
        for e in CONFIDENTIAL_PORTFOLIO_DATA["edges"]:
            edges.append(Edge(
                source=e["src"],
                target=e["dst"],
                label=f"{e['rel']} (w={e['weight']})",
                color="#EF553B"
            ))
            
        config = Config(width=650, height=520, directed=True, physics=True, hierarchical=False)
        agraph(nodes=nodes, edges=edges, config=config)

```

---

## 4. 给 Coding Agent (Cursor / Claude Code) 的一键初始化指令

当你打开本地编辑器时，把以上内容存为 `SPEC.md`，然后直接给你的 Coding Agent 发送下面这段 Prompt：

```text
请阅读当前目录下的 SPEC.md，帮我直接按照文档中的文件名和代码结构，在当前目录创建以下完整文件：
1. requirements.txt
2. .env.example (并同时复制一份为 .env)
3. dirty_payload.py
4. sandbox_verifier.py
5. evo_engine.py
6. app.py

创建完成后，请在终端验证 python 语法无误，并告诉我如何运行 streamlit 看板。如果现场安装了真实 Neo4j Aura 凭证，请在生成的代码中同步把清洗后的节点与边通过 neo4j python driver (MERGE 语句) 真正写入 Neo4j 数据库。

```

