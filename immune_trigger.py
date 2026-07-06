import os
import json
import base64
import requests
from e2b_code_interpreter import Sandbox

OPENAI_KEY    = os.environ["OPENAI_API_KEY"]
GH_TOKEN      = os.environ["GH_TOKEN"]
REPO          = os.environ["REPO"]
COMMIT_SHA    = os.environ["COMMIT_SHA"]
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")
LANGSMITH_TRACING = os.environ["LANGSMITH_TRACING"]
LANGSMITH_ENDPOINT = os.environ["LANGSMITH_ENDPOINT"]
LANGSMITH_API_KEY = os.environ["LANGSMITH_API_KEY"]
LANGSMITH_PROJECT = os.environ["LANGSMITH_PROJECT"] 

with open("test_output.txt", encoding="utf-8") as f:
    FAILURE_LOG = f.read()


def get_file_from_github(filepath):
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/contents/{filepath}",
        headers={"Authorization": f"token {GH_TOKEN}"},
        params={"ref": COMMIT_SHA}
    )
    return base64.b64decode(r.json()["content"]).decode("utf-8")


def push_fix_to_github(filepath, fixed_code, message):
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/contents/{filepath}",
        headers={"Authorization": f"token {GH_TOKEN}"}
    )
    file_sha = r.json()["sha"]
    requests.put(
        f"https://api.github.com/repos/{REPO}/contents/{filepath}",
        headers={"Authorization": f"token {GH_TOKEN}"},
        json={
            "message": message,
            "content": base64.b64encode(fixed_code.encode()).decode(),
            "sha": file_sha
        }
    )
    print(f"Fix pushed: {message}")


def notify_slack(message):
    if SLACK_WEBHOOK:
        requests.post(SLACK_WEBHOOK, json={"text": message})


import base64
from datetime import datetime, timezone

def read_immune_memory():
    """Read immune_memory.json from GitHub repo."""
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{REPO}/contents/immune_memory.json",
            headers={
                "Authorization": f"token {GH_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        memory = json.loads(content)
        file_sha = data["sha"]
        return memory, file_sha
    except Exception as e:
        print(f"Could not read immune_memory.json: {e}")
        return {"failure_count": 0, "history": []}, None


def update_immune_memory(memory: dict, file_sha: str, healed: bool, action: str):
    """Write updated immune_memory.json back to GitHub."""
    if healed:
        memory["failure_count"] = 0
        memory["last_healed_sha"] = COMMIT_SHA
        memory["last_healed_at"] = datetime.now(timezone.utc).isoformat()
    else:
        memory["failure_count"] = memory.get("failure_count", 0) + 1
        memory["last_failure_sha"] = COMMIT_SHA

    memory.setdefault("history", []).append({
        "sha": COMMIT_SHA,
        "action": action,
        "healed": healed,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    # Keep history to last 20 entries
    memory["history"] = memory["history"][-20:]

    content = base64.b64encode(
        json.dumps(memory, indent=2).encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": f"chore: immune memory [{action}] {'✅' if healed else '❌'} on {COMMIT_SHA[:7]}",
        "content": content
    }
    if file_sha:
        payload["sha"] = file_sha

    requests.put(
        f"https://api.github.com/repos/{REPO}/contents/immune_memory.json",
        headers={
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        },
        json=payload
    )
    print(f"Memory updated: failure_count={memory['failure_count']}, healed={healed}")


IMMUNE_CODE = '''
import subprocess
import os
import sys
import json
import subprocess
import importlib
import ast
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from smolagents import ToolCallingAgent, CodeAgent, OpenAIServerModel, tool
from fastapi.testclient import TestClient

# ── Load app ──────────────────────────────────────────────────────────────────
spec = importlib.util.spec_from_file_location("app", "/home/user/app.py")
app_module = importlib.util.module_from_spec(spec)
sys.modules["app"] = app_module
spec.loader.exec_module(app_module)
app = app_module.app
client = TestClient(app, raise_server_exceptions=False)

app_code = open("/home/user/app.py").read()

model = OpenAIServerModel(
    model_id="gpt-4o-mini",
    api_base="https://openai.vocareum.com/v1",
    api_key=os.environ["OPENAI_API_KEY"],
    temperature=0.4
)

failure_log   = os.environ.get("FAILURE_LOG", "")
failure_count = int(os.environ.get("FAILURE_COUNT", "0"))


def reload_app():
    global app, client
    spec = importlib.util.spec_from_file_location("app", "/home/user/app.py")
    app_module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = app_module
    spec.loader.exec_module(app_module)
    app = app_module.app
    client = TestClient(app, raise_server_exceptions=False)

import ast

def chunk_by_class_and_function(source_code: str) -> list[dict]:
    """
    Chunks source code purely by class and function boundaries using AST.
    Every chunk carries both the class name and function name it belongs to.
    """
    lines = source_code.splitlines()
    tree  = ast.parse(source_code)

    # ── Pass 1: Map every line to its class owner ─────────────────────────────
    class_ranges = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for lineno in range(node.lineno, node.end_lineno + 1):
                class_ranges[lineno] = node.name

    # ── Pass 2: Extract one chunk per function/method ─────────────────────────
    chunks = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # Is this function a method inside a class?
            parent_class = class_ranges.get(node.lineno)

            # Extract the exact lines for this function
            start  = node.lineno - 1      # 0-indexed
            end    = node.end_lineno       # 0-indexed exclusive
            source = "\\n".join(lines[start:end])

            # Build label — Class.method or just function
            if parent_class:
                label = f"{parent_class}.{node.name}"
            else:
                label = node.name

            chunks.append({
                "label":      label,            # e.g. "Save10Discount.apply"
                "class_name": parent_class,     # e.g. "Save10Discount" or None
                "func_name":  node.name,        # e.g. "apply"
                "func_sig":   lines[node.lineno - 1].strip(),
                "start_line": node.lineno,
                "end_line":   node.end_lineno,
                "source":     source,
            })

    return chunks

import chromadb
# Initialise ChromaDB inside the sandbox
chroma_client = chromadb.Client()  # in-memory

from chromadb import EmbeddingFunction, Documents, Embeddings
from openai import OpenAI as OpenAIClient

openai_client = OpenAIClient(
    base_url="https://openai.vocareum.com/v1",
    api_key=os.environ["OPENAI_API_KEY"]
)

# Stage 1 — Index all chunks into ChromaDB:
def index_chunks(source_code: str, collection) -> None:
    chunks = chunk_by_class_and_function(source_code)

    # ── Print all chunks so they appear in the Actions log ────────────────────
    print("\\n=== All chunks generated ===")
    for i, c in enumerate(chunks):
        print("Chunk " + str(i+1) + ": " + c["label"])
        print("  Class:      " + str(c["class_name"]))
        print("  Method:     " + c["func_name"])
        print("  Lines:      " + str(c["start_line"]) + "-" + str(c["end_line"]))
        print("  Signature:  " + c["func_sig"])
        print("  Source:")
        print(c["source"])
        print("")
    print("Total chunks: " + str(len(chunks)))
    print("=== End chunks ===\\n")

    # Embed manually using OpenAI
    embeddings = [
        openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=(
                "Function: " + c["label"] + "\\n"
                + "Signature: " + c["func_sig"] + "\\n"
                + "Class: " + str(c["class_name"]) + "\\n"
                + c["source"]
            )
        ).data[0].embedding
        for c in chunks
    ]

    collection.add(
        documents=[c["source"] for c in chunks],
        embeddings=embeddings,
        metadatas=[{
            "label":      c["label"],
            "class_name": c["class_name"] or "",
            "func_name":  c["func_name"],
            "func_sig":   c["func_sig"],
            "start_line": str(c["start_line"]),
            "end_line":   str(c["end_line"]),
        } for c in chunks],
        ids=[c["label"] for c in chunks]
    )

# Stage 2 — Query ChromaDB instead of manual cosine similarity:
def retrieve_top_k_chroma(query: str, collection, k: int = 3) -> list[dict]:
    # Embed query manually using OpenAI
    query_embedding = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=query
    ).data[0].embedding

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=collection.count(),
        include=["documents", "metadatas", "distances"]
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0]
    ):
        chunks.append({
            "source":     doc,
            "label":      meta["label"],
            "class_name": meta["class_name"] or None,
            "func_name":  meta["func_name"],
            "func_sig":   meta["func_sig"],
            "start_line": int(meta["start_line"]),
            "end_line":   int(meta["end_line"]),
            "score":      1 - dist  # chroma returns distance, convert to similarity
        })

    print("\\n=== ChromaDB scores for all chunks ===")
    for i, c in enumerate(chunks):
        print("  " + str(i+1) + ". " + c["label"]
              + "  score: " + str(round(c["score"], 4)))
    print("=== End scores ===\\n")

    return chunks[:k]
    

def replace_function(source: str,
                     func_name: str,
                     new_func: str,
                     class_name: str = None) -> str:
    
    """
    Replaces a function or method in source.
    If class_name is provided, only replaces the method inside that class.
    """

    tree  = ast.parse(source)
    lines = source.splitlines()

    class_ranges = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for lineno in range(node.lineno, node.end_lineno + 1):
                class_ranges[lineno] = node.name

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            parent_class = class_ranges.get(node.lineno)
            if class_name and parent_class != class_name:
                continue

            # Detect original indentation from first line of function
            original_indent = len(lines[node.lineno - 1]) \
                              - len(lines[node.lineno - 1].lstrip())
            indent = " " * original_indent

            # Re-indent the new function to match original
            new_lines = new_func.splitlines()
            if new_lines:
                # Use first non-empty line as base indent reference
                non_empty = [l for l in new_lines if l.strip()]
                if not non_empty:
                    continue
                returned_indent = len(non_empty[0]) - len(non_empty[0].lstrip())

                # ── Debug prints ──────────────────────────────────────────────────────────────
                print("original_indent=" + str(original_indent))
                print("returned_indent=" + str(returned_indent))
                print("first non_empty line='" + non_empty[0] + "'")
                print("func_name=" + func_name)
                # ─────────────────────────────────────────────────────────────────────────────

                reindented = []
                for line in new_lines:
                    if line.strip():
                        line_indent = len(line) - len(line.lstrip())
                        extra = max(0, line_indent - returned_indent)
                        reindented.append(indent + " " * extra + line.lstrip())
                    else:
                        reindented.append("")
                new_func = "\\n".join(reindented)

            start = node.lineno - 1
            end   = node.end_lineno
            lines[start:end] = new_func.splitlines()
            return "\\n".join(lines)

    return source


# ── Tools ─────────────────────────────────────────────────────────────────────
@tool
def check_health() -> dict:
    """
    Runs smoke tests against the order API.
    Returns a health report showing which scenarios pass or fail.
    """
    results = {}
    r1 = client.post("/order", json={"product_id": 1, "quantity": 2})
    results["basic_order"] = {
        "status_code": r1.status_code,
        "healthy": r1.status_code == 200 and r1.json().get("total") == 20.0,
        "response": r1.json()
    }
    r2 = client.post("/order", json={"product_id": 1, "quantity": 2, "coupon": "SAVE10"})
    results["save10_coupon"] = {
        "status_code": r2.status_code,
        "healthy": r2.status_code == 200 and r2.json().get("total") == 18.0,
        "response": r2.json()
    }
    r3 = client.post("/order", json={"product_id": 2, "quantity": 4, "coupon": "SAVE50"})
    results["save50_coupon"] = {
        "status_code": r3.status_code,
        "healthy": r3.status_code == 200 and r3.json().get("total") == 4.0,
        "response": r3.json()
    }
    return results


@tool
def save_test_to_file(content: str) -> str:
    """
    Saves generated pytest code to file.

    Args:
        content: Complete pytest source code as a string to save.
    """
    with open("/home/user/test_generated.py", "w") as f:
        f.write(content + "\\n")
    if "def test_" not in content:
        raise RuntimeError("No test functions found")
    return "Tests saved successfully to /home/user/test_generated.py"


@tool
def run_tests(test_code: str) -> dict:
    """
    Runs pytest on the generated test file inside the sandbox.

    Args:
        test_code: Complete pytest source code as a string to execute.
    """
    with open("/home/user/test_generated.py", "w") as f:
        f.write(test_code + "\\n")
    result = subprocess.run(
        ["pytest", "/home/user/test_generated.py", "-v", "--tb=short"],
        capture_output=True, text=True
    )
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr
    }

@tool
def patch_app(reason: str) -> str:
    """
    Uses ChromaDB vector store to find the right class and method, then LLM fixes it.

    Args:
        reason: Description of the fix being applied.
    """
    from smolagents.models import ChatMessage

    with open("/home/user/app.py", "r") as f:
        source = f.read()

    failure_log = os.environ.get("FAILURE_LOG", "")

    # ── Step 1: Index source into ChromaDB ────────────────────────────────────
    # Re-index on every run so stale chunks never persist
    try:
        chroma_client.delete_collection("app_chunks")
    except Exception:
        pass
    collection = chroma_client.create_collection(
    "app_chunks",
    metadata={"hnsw:space": "cosine"}
    )

    index_chunks(source, collection)

    # extract semantic query from failure log
    extract_prompt = (
        "Extract a concise description of what is failing from this CI log. "
        "Focus on: which test failed, what value was expected, what value was returned. "
        "Return only 1-2 sentences, no code, no pytest output.\\n\\n"
        + failure_log
    )
    extracted = model([ChatMessage(role="user", content=extract_prompt)])
    query = extracted.content.strip()
    print("\\nRAG Query: " + query)

    # ── Step 2: Query ChromaDB — replaces manual embed + cosine ───────────────
    candidates = retrieve_top_k_chroma(query, collection, k=collection.count())

    scored = []
    for c in candidates:
        score_prompt = (
            "Rate 0-10 how likely this function contains the bug described.\\n\\n"
            "Test failure:\\n" + query + "\\n\\n"
            "Function:\\n"
            "Function: " + c["label"] + "\\n"
            + "Class: " + str(c["class_name"]) + "\\n"
            + "Signature: " + c["func_sig"] + "\\n"
            + c["source"] + "\\n\\n"
            "Does this specific function directly contain the bug that causes the test failure?\\n"
            "Score 9-10: this function has an obvious bug directly causing the failure.\\n"
            "Score 5-8: this function might be related but the bug is not obvious here.\\n"
            "Score 0-4: this function looks correct, not the source of the failure.\\n"
            "Return ONLY a single integer 0-10. Nothing else."
        )
        response = model([ChatMessage(role="user", content=score_prompt)])
        try:
            score = float(response.content.strip())
        except:
            score = 0.0
        c["rerank_score"] = score
        scored.append(c)

    reranked = sorted(scored, key=lambda x: x["rerank_score"], reverse=True)

    print("\\n=== Cross Encoder scores for all chunks ===")
    for c in reranked:
        print(f"  {c['label']}  rerank: {c['rerank_score']:.1f}")
    print("=== End scores ===\\n")

    top_2 = [c for c in reranked if c["rerank_score"] > 0]
    if not top_2:
        top_2 = reranked[:1]

    # ── Add these prints to see what chunk was retrieved ──────────────────────────
    print("\\n=== RAG Retrieval Result ===")
    for i, c in enumerate(top_2):
        print(f"Chunk {i+1}: {c['label']}")
        print(f"  Class:    {c['class_name']}")
        print(f"  Method:   {c['func_name']}")
        print(f"  Lines:    {c['start_line']}-{c['end_line']}")
        print(f"  Score:    {c['score']:.4f}") # ChromaDB cosine score
        print(f"  Rerank:   {c['rerank_score']:.4f}") # Cross encoder score
        print(f"  Source:\\n{c['source']}")
    print("=== End RAG Result ===\\n")

    # ── Step 3, 4, 5: Fix each retrieved chunk independently ──────────────────
    fixed_source = source
    for c in top_2:
        # Re-extract the current source of this function from fixed_source
        # so the LLM sees what the function looks like NOW not at index time
        current_chunks = chunk_by_class_and_function(fixed_source)
        current_chunk = next(
            (ch for ch in current_chunks
            if ch["func_name"] == c["func_name"]
            and ch["class_name"] == (c["class_name"] or "")),
            c  # fallback to original if not found
        )

        fix_prompt = (
            f"This method contains a bug:\\n\\n"
    	    f"# Class:     {str(current_chunk['class_name'])}\\n"
    	    f"# Method:    {current_chunk['func_name']}\\n"
    	    f"{current_chunk['source']}\\n\\n"
    	    f"CI failure output:\\n{failure_log}\\n\\n"
    	    f"Reason: {reason}\\n\\n"
      	    "Fix ONLY this specific method. "
            "If this method looks correct and has no bug, return it UNCHANGED. "
            "Do NOT modify healthy functions to compensate for bugs elsewhere. "
            "Do NOT add any new lines, logic, or improvements."
    	    "Preserve ALL comments, blank lines, and formatting exactly as in the original. "
    	    "Do NOT reformat, clean up, or remove any comments. "
    	    "For each changed line, add an inline comment explaining what was changed. "
    	    "Return ONLY the corrected method — not the full file."
	    )

        response   = model([ChatMessage(role="user", content=fix_prompt)])
        fixed_func = response.content.strip()

        # ── Debug print ───────────────────────────────────────────────────────────────
        print("new_func repr='" + repr(fixed_func[:200]) + "'")
        # ─────────────────────────────────────────────────────────────────────────────

        if fixed_func.startswith("```"):
            fixed_func = "\\n".join(
                line for line in fixed_func.split("\\n")
                if not line.startswith("```")
            ).strip("\\n")
        print("After fence strip: " + repr(fixed_func[:100]))

        # Strip prompt headers the LLM sometimes echoes back
        fixed_func = "\\n".join(
            line for line in fixed_func.split("\\n")
            if not line.strip().startswith("# Class:")
            and not line.strip().startswith("# Method:")
            and not line.strip().startswith("# Function:")
            and not line.strip().startswith("# Signature:")
            and not line.strip().startswith("# Location:")
        ).strip("\\n")

        fixed_source = replace_function(
            fixed_source,
            c["func_name"],
            fixed_func,
            class_name=c["class_name"]
        )


    # ── Step 6: Validate syntax ────────────────────────────────────────────────
    try:
        compile(fixed_source, "app.py", "exec")
    except SyntaxError as e:
        return f"Patch aborted — invalid Python: {e}"

    # ── Step 7: Write fixed files ──────────────────────────────────────────────
    with open("/home/user/app.py", "w") as f:
        f.write(fixed_source)
    with open("/home/user/fixed_app.py", "w") as f:
        f.write(fixed_source)

    reload_app()
    return f"Patched: {reason}"


@tool
def rollback_app(reason: str) -> str:
    """
    Rolls back app.py to the last commit where CI workflow passed.

    Args:
        reason: Description of why the rollback is being performed.
    """
    import urllib.request
    import json as _json
    import base64 as _base64

    gh_token = os.environ.get("GH_TOKEN", "")
    repo     = os.environ.get("REPO", "")
    headers  = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "digital-immune-system"
    }

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/commits?per_page=20",
        headers=headers
    )
    with urllib.request.urlopen(req) as r:
        commits = _json.loads(r.read())

    stable_sha = None
    stable_message = None
    for commit in commits:
        sha = commit["sha"]
        message = commit["commit"]["message"]
        try:
            req2 = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/actions/runs?head_sha={sha}",
                headers=headers
            )
            with urllib.request.urlopen(req2) as r:
                data = _json.loads(r.read())
            workflow_runs = data.get("workflow_runs", [])
            if not workflow_runs:
                continue
            all_passed = all(
                run["status"] == "completed" and run["conclusion"] == "success"
                for run in workflow_runs
            )
            if all_passed:
                stable_sha = sha
                stable_message = message
                break
        except Exception:
            continue

    if not stable_sha:
        return "Could not find any stable commit in recent history"

    req3 = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/app.py?ref={stable_sha}",
        headers=headers
    )
    with urllib.request.urlopen(req3) as r:
        file_data = _json.loads(r.read())
    stable_code = _base64.b64decode(file_data["content"]).decode("utf-8")

    with open("/home/user/app.py", "w") as f:
        f.write(stable_code)
    with open("/home/user/fixed_app.py", "w") as f:
        f.write(stable_code)
    reload_app()
    return f"Rolled back to stable commit {stable_sha[:7]} ({stable_message[:50]}): {reason}"


@tool
def escalate(reason: str) -> str:
    """
    Escalates the failure to the on-call engineer when auto-healing fails.

    Args:
        reason: Full description of the unresolved failure to escalate.
    """
    msg = f"ESCALATED TO ON-CALL: {reason}"
    print(msg)
    return msg


# ── Agents ────────────────────────────────────────────────────────────────────
monitor_agent    = ToolCallingAgent(name="MonitorAgent",    model=model, tools=[check_health], max_steps=1)
testgen_agent    = ToolCallingAgent(name="TestGenAgent",    model=model, tools=[save_test_to_file], max_steps=3)
testrunner_agent = ToolCallingAgent(name="TestRunnerAgent", model=model, tools=[run_tests], max_steps=2)
guardian_agent   = CodeAgent(name="GuardianAgent",          model=model, tools=[])
healer_agent     = ToolCallingAgent(name="HealerAgent",     model=model, tools=[patch_app, rollback_app, escalate], max_steps=3)


# ── State ─────────────────────────────────────────────────────────────────────
class ImmuneState(TypedDict):
    health: str
    all_healthy: bool
    test_code: str
    test_result: str
    decision: str
    heal_result: str
    recovered: bool
    stable_sha: Optional[str]
    retest_result: str

def print_state(state: ImmuneState, node_name: str):
    print(f"\\n{'='*50}")
    print(f"State after {node_name}:")
    print(f"  health      : {state['health'][:80] if state['health'] else 'empty'}...")
    print(f"  all_healthy : {state['all_healthy']}")
    print(f"  decision    : {state['decision']}")
    print(f"  recovered   : {state['recovered']}")
    print(f"  test_code   : {state['test_code'][:80] if state['test_code'] else 'empty'}...")
    print(f"  test_result : {state['test_result'][:80] if state['test_result'] else 'empty'}...")
    print(f"  heal_result : {state['heal_result'][:80] if state['heal_result'] else 'empty'}...")
    print(f"  retest_result : {state['retest_result'][:80] if state['retest_result'] else 'empty'}...")
    print(f"{'='*50}\\n")    


def get_health_from_memory(agent):
    for step in reversed(agent.memory.steps):
        if hasattr(step, "observations") and step.observations:
            obs = step.observations
            if isinstance(obs, dict):
                return obs
            if isinstance(obs, str) and "basic_order" in obs:
                try:
                    return ast.literal_eval(obs)
                except Exception:
                    pass
    return None    
    
# ── Nodes ─────────────────────────────────────────────────────────────────────
def monitor_node(state: ImmuneState) -> ImmuneState:
    print("\\nMonitorAgent scanning...")
    monitor_agent.run("Run health checks on the order API.")
    health = get_health_from_memory(monitor_agent)
    print(f"Health: {health}")
    all_healthy = isinstance(health, dict) and all(
        v.get("healthy") for v in health.values() if isinstance(v, dict)
    )
    new_state = {**state, "health": str(health), "all_healthy": all_healthy}
    print_state(new_state, "monitor_node")
    return new_state


def testgen_node(state: ImmuneState) -> ImmuneState:
    print("\\nTestGenAgent generating tests...")
    prompt = (
        f"A regression has been detected.\\n\\n"
        f"Application source code:\\n```python\\n{app_code}\\n```\\n\\n"
        f"CI failure log:\\n{failure_log}\\n\\n"
        f"Health report:\\n{state[\'health\']}\\n\\n"
        "Generate pytest tests that reproduce the failing scenarios.\\n"
        "IMPORTANT: The app module is named 'app' — use: from app import app\\n"
        "IMPORTANT: Use TestClient from fastapi.testclient\\n"
        "IMPORTANT: Assert ONLY specific response fields — do NOT assert the full response dict.\\n"
        "IMPORTANT: Use ONLY the expected values from the CI failure log assertions — not the actual values.\\n"
        "Infer the endpoints, HTTP methods, and request payloads from the Application source code.\\n"
        "Call save_test_to_file when done."
    )
    testgen_agent.run(prompt)
    with open("/home/user/test_generated.py", "r") as f:
        test_code = f.read()
    new_state = {**state, "test_code": test_code}
    print_state(new_state, "testgen_node")
    return new_state


def testrunner_node(state: ImmuneState) -> ImmuneState:
    print("\\nTestRunnerAgent running tests...")
    test_result = testrunner_agent.run(
        f"Run these tests and report results:\\n{state[\'test_code\']}"
    )
    print(f"Result: {test_result}")
    new_state = {**state, "test_result": str(test_result)}
    print_state(new_state, "testrunner_node")
    return new_state


def guardian_node(state: ImmuneState) -> ImmuneState:
    print("\\nGuardianAgent deciding...")
    prompt = (
        f"A regression has been detected in the application.\\n"
        f"Health: {state[\'health\']}\\n"
        f"Test result: {state[\'test_result\']}\\n"
        f"CI failure log: {failure_log}\\n"
        f"Times failed before: {failure_count}\\n\\n"
        "You MUST follow these rules strictly — no exceptions:\\n"
        f"failure_count is {failure_count}.\\n"
        "PATCH    if failure_count == 0 — attempt an automated fix\\n"
        "ROLLBACK if failure_count >= 1 — previous fix attempt failed, restore last stable commit\\n"
        "ESCALATE if failure_count >= 3 — auto-healing not working, page on-call\\n\\n"
        "Return ONLY one word: PATCH, ROLLBACK, or ESCALATE"
    )
    decision_raw = guardian_agent.run(prompt)
    decision = "ROLLBACK"
    for word in ["ESCALATE", "PATCH", "ROLLBACK"]:
        if word in str(decision_raw).upper():
            decision = word
            break
    print(f"Decision: {decision}")
    new_state = {**state, "decision": decision}
    print_state(new_state, "guardian_node")
    return new_state


def healer_node(state: ImmuneState) -> ImmuneState:
    action = state["decision"]
    print(f"\\nHealerAgent executing {action}...")
    prompt = (
        f"Decision: {action}\\n"
        f"Health: {state[\'health\']}\\n"
        f"Test result: {state[\'test_result\']}\\n\\n"
        f"CI failure log: {failure_log}\\n\\n"
        "You MUST call ONLY ONE tool based on the decision.\\n"
        f"The decision is: {action}\\n\\n"
        "If PATCH    - call patch_app only\\n"
        "If ROLLBACK - call rollback_app only\\n"
        "If ESCALATE - call escalate only\\n\\n"
        f"Call ONLY the tool that matches: {action}"
    )
    heal_result = healer_agent.run(prompt)
    import re
    sha_match = re.search(r'stable commit ([a-f0-9]{7})', str(heal_result))
    stable_sha = sha_match.group(1) if sha_match else None
    new_state = {**state, "heal_result": str(heal_result), "stable_sha": stable_sha}
    print_state(new_state, "healer_node")
    return new_state


def verify_node(state: ImmuneState) -> ImmuneState:
    print("\\nVerifying recovery...")
    monitor_agent.run("Run health checks again and confirm recovery.")
    health_after = get_health_from_memory(monitor_agent)
    recovered = isinstance(health_after, dict) and all(
        v.get("healthy") for v in health_after.values() if isinstance(v, dict)
    )
    print("Recovered" if recovered else "Still degraded")
    new_state={**state, "recovered": recovered, "all_healthy": recovered}
    print_state(new_state, "verify_node")
    return new_state

def retest_node(state: ImmuneState) -> ImmuneState:
    print("\\nRetestAgent re-running tests after heal...")
    retest_result = testrunner_agent.run(
        f"Re-run these tests and report results:\\n{state[\'test_code\']}"
    )
    print(f"Retest result: {retest_result}")
    new_state = {**state, "retest_result": str(retest_result)}
    print_state(new_state, "retest_node")
    return new_state    

# ── Conditional edge ──────────────────────────────────────────────────────────
def route_after_monitor(state: ImmuneState):
    return "testgen" if not state["all_healthy"] else END


# ── Build graph ───────────────────────────────────────────────────────────────
graph = StateGraph(ImmuneState)

graph.add_node("monitor",    monitor_node)
graph.add_node("testgen",    testgen_node)
graph.add_node("testrunner", testrunner_node)
graph.add_node("guardian",   guardian_node)
graph.add_node("healer",     healer_node)
graph.add_node("verify",     verify_node)
graph.add_node("retest", retest_node)

graph.set_entry_point("monitor")

graph.add_conditional_edges("monitor", route_after_monitor,
    {"testgen": "testgen", END: END})
graph.add_edge("testgen",    "testrunner")
graph.add_edge("testrunner", "guardian")
graph.add_edge("guardian",   "healer")
graph.add_edge("healer",     "verify")
graph.add_edge("verify",     "retest")
graph.add_edge("retest",  END)

immune_graph = graph.compile()

# ── Run ───────────────────────────────────────────────────────────────────────
print("\\nDIGITAL IMMUNE SYSTEM ACTIVATED\\n")

initial_state: ImmuneState = {
    "health": "",
    "all_healthy": False,
    "test_code": "",
    "test_result": "",
    "decision": "NONE",
    "heal_result": "",
    "recovered": False,
    "stable_sha": None,
    "retest_result": ""
}

final_state = immune_graph.invoke(initial_state)

with open("/home/user/result.json", "w") as f:
    json.dump({
        "action": final_state["decision"],
        "recovered": final_state["recovered"],
        "stable_sha": final_state["stable_sha"]
    }, f)
'''

print(f"Fetching app.py from GitHub at {COMMIT_SHA[:7]}...")
app_code = get_file_from_github("app.py")

print("Spinning up e2b sandbox...")
with Sandbox.create() as sandbox:

    sandbox.commands.run(
        "pip install fastapi pytest httpx httpx2 smolagents openai python-multipart langgraph langchain langchain_openai langgraph chromadb numpy",
        timeout=180
    )

    sandbox.files.write("/home/user/app.py", app_code)
    sandbox.files.write("/home/user/immune_system.py", IMMUNE_CODE)

    memory, memory_sha = read_immune_memory()
    FAILURE_COUNT = memory.get("failure_count", 0)
    print(f"Failure count from memory: {FAILURE_COUNT}")

    result = sandbox.commands.run(
        "cd /home/user && python immune_system.py",
        timeout=600,
        envs={
            "OPENAI_API_KEY": OPENAI_KEY,
            "GH_TOKEN":       GH_TOKEN,
            "REPO":           REPO,
            "CURRENT_SHA":    COMMIT_SHA,
            "FAILURE_COUNT":  str(FAILURE_COUNT),
            "FAILURE_LOG":    FAILURE_LOG,
            "LANGSMITH_TRACING": LANGSMITH_TRACING,
            "LANGSMITH_ENDPOINT": LANGSMITH_ENDPOINT,
            "LANGSMITH_API_KEY": LANGSMITH_API_KEY,
            "LANGSMITH_PROJECT": LANGSMITH_PROJECT
        }        
    )

    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    try:
        result_json = sandbox.files.read("/home/user/result.json")
        outcome = json.loads(result_json)
    except Exception:
        outcome = {"action": "UNKNOWN", "recovered": False}

    print(f"Outcome: {outcome}")

    update_immune_memory(
        memory,
        memory_sha,
        healed=outcome["recovered"],
        action=outcome["action"]
    )


    if outcome["action"] in ["PATCH", "ROLLBACK"]:
        try:
            fixed_code = sandbox.files.read("/home/user/fixed_app.py")

            # Verify it's valid Python before pushing
            if "ESCALATE" in fixed_code and len(fixed_code) < 50:
                print("ERROR: fixed_app.py contains invalid content, skipping push")
            else:
                # Build commit message
                if outcome["action"] == "ROLLBACK" and outcome.get("stable_sha"):
                    message = f"fix: auto-healed [ROLLBACK] triggered by {COMMIT_SHA[:7]} restored to {outcome['stable_sha']}"
                else:
                    message = f"fix: auto-healed [{outcome['action']}] on {COMMIT_SHA[:7]}"
                push_fix_to_github(
                    "app.py",
                    fixed_code,
                    message
                )
                notify_slack(
                    f":white_check_mark: *Digital Immune System — Auto-Healed*\n"
                    f">*Repo:* `{REPO}`\n"
                    f">*Commit:* `{COMMIT_SHA[:7]}`\n"
                    f">*Action:* `{outcome['action']}`\n"
                    f">*Recovered:* {'Yes ✅' if outcome['recovered'] else 'No ❌'}\n"
                    f">*Fix commit:* `{message}`"
                )
        except Exception as e:
            print(f"Could not push fix: {e}")

    elif outcome["action"] == "ESCALATE":
        notify_slack(
            f":rotating_light: *Digital Immune System — ESCALATION*\n"
            f">*Repo:* `{REPO}`\n"
            f">*Commit:* `{COMMIT_SHA[:7]}`\n"
            f">*Failure count:* `{memory.get('failure_count', '?')}`\n"
            f">*Reason:* Auto-healing failed after 3 attempts\n"
            f">*Action required:* Manual intervention needed\n"
            f">*CI run:* https://github.com/{REPO}/actions"
        )