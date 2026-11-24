"""What the demos share: the processes they start, the chain they talk to,
the keys they make, the steps they record, the node's jobs, and the
reconciliation of every figure against the independent calculator.

A demo (`payments.py`, `rewards.py`, `buyback.py`) calls `use(manifest)`
first, then `from common import *`; `full.py` builds on `rewards.py` and
`buyback.py`. `demo/run` chooses the demo.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "calculator"))
import ledgercase  # noqa: E402

CACHE = ROOT / ".cache" / "demo"
RUN = CACHE / "run"
ZERO = "0x" + "0" * 40

MANIFEST: dict = {}
VECTORS: dict = {}
CASES: dict = {}
PARAMS: dict = {}


def use(manifest_name: str) -> None:
    """Read the stage's manifest and the shared examples it names."""
    MANIFEST.update(json.loads((ROOT / "deployments" / manifest_name).read_text()))
    VECTORS.update(json.loads((ROOT / MANIFEST["vectors"]).read_text()))
    CASES.update({c["id"]: c for c in VECTORS["cases"]})
    PARAMS.update(VECTORS["parameters"])


class NotRun(Exception):
    """The demo could not run: exit 2."""


class Failed(Exception):
    """A figure differed or a behaviour was missing: exit 1."""


LOG = None


def open_log() -> None:
    global LOG
    shutil.rmtree(RUN, ignore_errors=True)
    (RUN / "logs").mkdir(parents=True)
    LOG = open(RUN / "demo.log", "w")


def where(path: Path) -> Path:
    """A path as the run prints it: from the repository when it is inside it."""
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def say(text: str = "") -> None:
    print(text, flush=True)
    if LOG:
        LOG.write(text + "\n")
        LOG.flush()


def money(v) -> str:
    return f"{int(v):,}"


def lpt(v) -> str:
    """Wei of LPT as LPT with all 18 decimals."""
    v = int(v)
    return f"{v // 10**18:,}.{v % 10**18:018d}"


# --- processes --------------------------------------------------------------

PROCS: dict[str, subprocess.Popen] = {}
CONTAINERS: list[str] = []
# Descriptors every program the run starts keeps open: none in the demo; in
# demo/public.py the state directory's lock, so the lock is held for as long
# as any program that may send with the run's keys is alive, even if the run
# itself is killed.
KEEP_FDS: tuple[int, ...] = ()


def start(name: str, argv: list[str], cwd: Path | None = None) -> None:
    log = open(RUN / "logs" / f"{name}.log", "a")
    PROCS[name] = subprocess.Popen(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True, pass_fds=KEEP_FDS)


def wait_up(name: str, metrics: int, seconds: float = 60) -> None:
    """Wait until the node process `name` answers /healthz on its metrics
    port. If it exits first or never answers, fail naming it, its log and
    the log's last line, rather than at the first job sent to it."""
    log = RUN / "logs" / f"{name}.log"
    end = time.time() + seconds
    while time.time() < end:
        code = PROCS[name].poll()
        if code is not None:
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{metrics}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.2)
    lines = log.read_text(errors="replace").strip().splitlines() if log.is_file() else []
    how = f"exited with status {code} at startup" if code is not None else f"did not answer /healthz within {seconds:.0f} s"
    shown = where(log)
    raise Failed(f"{name} {how}: {lines[-1] if lines else '(its log is empty)'}; its log is {shown}")


def stop(name: str) -> None:
    p = PROCS.pop(name, None)
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def cleanup() -> None:
    """Stop everything at once, then wait up to 10 s before killing what is left."""
    for p in PROCS.values():
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.time() + 10
    for p in PROCS.values():
        try:
            p.wait(max(0.1, end - time.time()))
        except subprocess.TimeoutExpired:
            pass
    for name in list(PROCS):
        stop(name)
    for c in CONTAINERS:
        subprocess.run(["docker", "rm", "-f", c], capture_output=True)
    CONTAINERS.clear()


def sh(argv: list[str], cwd: Path | None = None, check: bool = True, env: dict | None = None) -> str:
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env=env, pass_fds=KEEP_FDS)
    if check and done.returncode != 0:
        raise Failed(f"{' '.join(argv[:3])} ... failed: {(done.stderr or done.stdout).strip()[-600:]}")
    return done.stdout.strip()


# --- preflight and fetching ---------------------------------------------------

BASE = int(os.environ.get("LP2_DEMO_PORT_BASE", "47700"))
PORT = {"chain": BASE, "orch": BASE + 1, "orch_metrics": BASE + 2, "signer_p": BASE + 3,
        "signer_p_metrics": BASE + 4, "signer_p2": BASE + 5, "signer_p2_metrics": BASE + 6, "model": BASE + 7,
        "orch_m": BASE + 8, "orch_m_metrics": BASE + 9, "orch_k": BASE + 10, "orch_k_metrics": BASE + 11,
        "orch_l": BASE + 12, "orch_l_metrics": BASE + 13, "explorer": BASE + 14, "explorer_restarted": BASE + 15,
        "provider": BASE + 16}


def preflight(ports: list[str], docker: bool = True) -> None:
    missing = [t for t in ("git", "go", "forge", "anvil", "cast") + (("docker",) if docker else ()) if not shutil.which(t)]
    if missing:
        raise NotRun(f"missing {', '.join(missing)} (see README: Run the demo)")
    if docker and subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        raise NotRun("docker is installed but its daemon is not reachable by this user")
    for name in ports:
        port = PORT[name]
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                raise NotRun(f"port {port} ({name}) is in use; set LP2_DEMO_PORT_BASE to move the demo's ports")


def fetch_node() -> Path:
    node = MANIFEST["node"]
    commit = node["commit"]
    home = CACHE / f"node-{commit[:12]}"
    bins = {p: home / "bin" / p for p in node["programs"]}
    if all(b.is_file() for b in bins.values()):
        return home / "bin"
    src = home / "src"
    shutil.rmtree(home, ignore_errors=True)
    src.mkdir(parents=True)
    source = os.environ.get("LP2_NODE_SOURCE", node["source"])
    say(f"  fetching the node at {commit[:12]} from {source}")
    try:
        sh(["git", "init", "-q"], cwd=src)
        sh(["git", "fetch", "-q", "--depth", "1", source, commit], cwd=src)
        sh(["git", "checkout", "-q", "FETCH_HEAD"], cwd=src)
    except Failed as e:
        raise NotRun(f"could not fetch the node: {e}")
    if sh(["git", "rev-parse", "HEAD"], cwd=src) != commit:
        raise NotRun("the fetched node is not the pinned commit")
    say("  building livepeer-orchestrator and livepeer-signer (the first run takes a few minutes)")
    for p, b in bins.items():
        try:
            sh(["go", "build", "-o", str(b), f"./cmd/{p}"], cwd=src, env=GO_ENV)
        except Failed as e:
            raise NotRun(f"could not build the node: {e}")
    return home / "bin"


def fetch_model() -> Path:
    model = MANIFEST["model"]
    path = CACHE / "model" / f"{model['sha256']}.gguf"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        say(f"  downloading the model ({model['name']}, about 145 MB)")
        part = path.with_suffix(".part")
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(model["url"], timeout=60) as r, open(part, "wb") as f:
                while chunk := r.read(1 << 20):
                    digest.update(chunk)
                    f.write(chunk)
        except (urllib.error.URLError, OSError) as e:
            raise NotRun(f"could not download the model: {e}")
        if digest.hexdigest() != model["sha256"]:
            part.unlink()
            raise NotRun("the downloaded model does not have the pinned sha256")
        part.rename(path)
    image = model["server_image"]
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        say(f"  pulling the model server image {image}")
        if subprocess.run(["docker", "pull", "-q", image], capture_output=True).returncode != 0:
            raise NotRun(f"could not pull {image}")
    return path


# Go builds without its saved settings (go env -w, the file GOENV names) or
# the caller's GOFLAGS, either of which could add a build tag or flag.
GO_ENV = {**{k: v for k, v in os.environ.items() if k != "GOFLAGS"}, "GOENV": "off", "GOWORK": "off"}

# Each helper program: its binary name and the package it is built from.
PROGRAMS = {"lp2-fund": "./cmd/lp2-fund", "lp2-keeper": "./cmd/lp2-keeper", "lp2-params": "./cmd/lp2-params",
            "lp2-stake": "./cmd/lp2-stake", "lp2-explorer": "./cmd/lp2-explorer",
            "lp2-validators": "./validators"}


def build_ours() -> Path:
    try:
        sh(["forge", "build", "--root", str(ROOT / "contracts")])
        bin_dir = RUN / "bin"
        for p, pkg in PROGRAMS.items():
            sh(["go", "build", "-o", str(bin_dir / p), pkg], cwd=ROOT / "programs", env=GO_ENV)
    except Failed as e:
        raise NotRun(f"could not build the contracts or programs: {e}")
    return bin_dir


def start_chain() -> None:
    chain = MANIFEST["chain"]
    start("anvil", ["anvil", "--port", str(PORT["chain"]), "--chain-id", str(chain["chain_id"]),
                    "--block-time", str(chain["block_time_seconds"]), "--slots-in-an-epoch", "1"])
    for _ in range(100):
        try:
            block_number()
            RPC_FILE.write_text(RPC)
            return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.1)
    raise NotRun("the local chain did not start")


def start_model(model: Path) -> None:
    container = f"lp2-demo-llm-{BASE}"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    CONTAINERS.append(container)
    sh(["docker", "run", "-d", "--rm", "--name", container, "-p", f"127.0.0.1:{PORT['model']}:8080",
        "-v", f"{model.parent}:/models:ro", MANIFEST["model"]["server_image"],
        "-m", f"/models/{model.name}", "--host", "0.0.0.0", "--port", "8080", "-t", "4"])
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT['model']}/health", timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)
    raise NotRun("the model server did not become healthy")


def start_orchestrator(name: str, node: str, node_bin: Path, keyfile: Path, pw: Path, port: int, metrics: int,
                       runner: tuple[str, str, int] | None = None) -> None:
    """The unmodified node's orchestrator for `node`, serving at the
    manifest's price the tiny model, or `runner`: (id, app, port) of a
    runner on this machine that answers GET /health."""
    price, chain = MANIFEST["price"], MANIFEST["chain"]
    rid, app, rport = runner or ("llm", "chat", PORT["model"])
    d = RUN / name
    d.mkdir(exist_ok=True)  # a resumed public run keeps the node's redeemer database
    (d / "runners.toml").write_text(f"""[[Runners]]
ID = "{rid}"
RunnerURL = "http://127.0.0.1:{rport}"
App = "{app}"
Mode = "single-shot"
Status = "ready"
Capacity = 4
HealthURL = "http://127.0.0.1:{rport}/health"
HealthCode = 200
[Runners.PriceInfo]
Price = {price['job_price_usd']}
Currency = "USD"
Unit = "{price['unit']}"
""")
    start(name, [str(node_bin / "livepeer-orchestrator"), "--config=", "--network", "lp2-demo",
                 "--data-dir", str(d), "--listen", f"127.0.0.1:{port}",
                 "--metrics-listen", f"127.0.0.1:{metrics}",
                 "--runner-config", str(d / "runners.toml"), "--runner-grants", "127.0.0.1",
                 "--health-grants", f"127.0.0.1:{rport}",
                 "--keystore-file", str(keyfile), "--keystore-password-file", str(pw),
                 "--rpc-url-file", str(RPC_FILE), "--chain-id", str(chain["chain_id"]),
                 "--controller-address", C["Controller"], "--redeemer-db", str(d / "payments.sqlite"),
                 "--wei-per-usd", price["wei_per_usd"], "--ticket-face-value", str(ticket_face()),
                 "--ticket-win-prob", price["ticket_win_prob"]], cwd=d)
    wait_up(name, metrics)


def start_signer(payer: str, node_bin: Path, keyfile: Path, pw: Path, port: int, metrics: int, orchs: list[int]) -> None:
    """The unmodified node's signer for `payer`, allowed to pay the orchestrators on `orchs`."""
    price, chain, lim = MANIFEST["price"], MANIFEST["chain"], MANIFEST["signer_limits"]
    d = RUN / f"signer-{payer}"
    d.mkdir(exist_ok=True)
    start(f"signer-{payer}", [str(node_bin / "livepeer-signer"), "--config=", "--network", "lp2-demo", "--data-dir", str(d),
                              "--listen", f"127.0.0.1:{port}", "--metrics-listen", f"127.0.0.1:{metrics}",
                              "--keystore-file", str(keyfile), "--keystore-password-file", str(pw),
                              "--rpc-url-file", str(RPC_FILE), "--chain-id", str(chain["chain_id"]),
                              "--controller-address", C["Controller"], "--wei-per-usd", price["wei_per_usd"],
                              "--max-hourly-price", lim["max_hourly_price_usd"], "--max-fixed-price", lim["max_fixed_price_usd"],
                              "--max-ticket-ev", lim["max_ticket_ev"], "--max-batch-ev", lim["max_batch_ev"],
                              "--orchestrators", ",".join(f"http://127.0.0.1:{p}" for p in orchs),
                              "--discovery-grants", ",".join(f"127.0.0.1:{p}" for p in orchs)], cwd=d)
    wait_up(f"signer-{payer}", metrics)


def ticket_face() -> int:
    """The job price in micro-USDC plus 1."""
    price = MANIFEST["price"]
    return int(float(price["job_price_usd"]) * int(price["wei_per_usd"]) + 0.5) + 1


# --- the chain ----------------------------------------------------------------

RPC = f"http://127.0.0.1:{PORT['chain']}"
# The chain's URL for the node's programs, which take it only from a file.
# Written once, by start_chain: rewriting it while a node starts let the node
# read it empty and exit (an empty file leaves the URL unset).
RPC_FILE = RUN / "rpc-url"
SELECTORS: dict[str, str] = {}
# The first block worth reading: 0 on the demo's fresh chain, the deployment
# block on a chain that was running before (demo/public.py). Logs and the
# scan for strays start here.
FROM_BLOCK = 0
# Calls sent in one JSON-RPC batch; a hosted endpoint may take fewer.
BATCH = 500
# Methods that change nothing, so a batch of them is sent again after a
# dropped connection or a server error. A batch refused with 429 (rate
# limited) was not served, so any batch is sent again after one.
_READ_METHODS = {
    "eth_blockNumber", "eth_call", "eth_chainId", "eth_getBlockByNumber", "eth_getCode", "eth_getBalance",
    "eth_getLogs", "eth_getTransactionByHash", "eth_getTransactionCount", "eth_getTransactionReceipt",
}
RETRY_WAITS = (1, 2, 4, 8, 15, 30)


def rpc_batch(calls: list[tuple[str, list]]) -> list:
    out = []
    for i in range(0, len(calls), BATCH):
        chunk = calls[i:i + BATCH]
        body = json.dumps([{"jsonrpc": "2.0", "id": j, "method": m, "params": p}
                           for j, (m, p) in enumerate(chunk)]).encode()
        reads = all(m in _READ_METHODS for m, _ in chunk)
        for wait in (*RETRY_WAITS, None):
            req = urllib.request.Request(RPC, data=body, headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    answers = json.loads(r.read())
                if isinstance(answers, list):
                    answers.sort(key=lambda a: a["id"])
                    break
                # One error for the whole batch, such as a rate limit some
                # endpoints answer with status 200.
                if wait is None or not reads:
                    raise Failed(f"the endpoint refused the batch: {answers.get('error', answers)}")
            except urllib.error.HTTPError as e:
                if wait is None or not (e.code == 429 or (reads and e.code >= 500)):
                    raise
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if wait is None or not reads:
                    raise
            time.sleep(wait)
        out += answers
    return out


def rpc(method: str, params: list):
    a = rpc_batch([(method, params)])[0]
    if "error" in a:
        raise Failed(f"{method}: {a['error']}")
    return a["result"]


def sel(signature: str) -> str:
    if signature not in SELECTORS:
        SELECTORS[signature] = sh(["cast", "sig", signature])
    return SELECTORS[signature]


def topic(signature: str) -> str:
    if signature not in SELECTORS:
        SELECTORS[signature] = sh(["cast", "keccak", signature])
    return SELECTORS[signature]


def calldata(signature: str, *args) -> str:
    words = "".join(f"{int(a, 16) if isinstance(a, str) else a:064x}" for a in args)
    return sel(signature) + words


def calls(items: list[tuple[str, str, tuple]], block="latest") -> list[int]:
    """eth_call each (contract, signature, args) at one block; the first word of each answer."""
    tag = hex(block) if isinstance(block, int) else block
    answers = rpc_batch([("eth_call", [{"to": to, "data": calldata(sig, *args)}, tag]) for to, sig, args in items])
    out = []
    for a, item in zip(answers, items):
        if "error" in a:
            raise Failed(f"eth_call {item[1]}: {a['error']}")
        out.append(int(a["result"][2:66], 16))
    return out


def words(to: str, signature: str, *args, block="latest") -> list[int]:
    """Every word a static answer holds, such as earmarkOf's (amount, pending, ready round)."""
    tag = hex(block) if isinstance(block, int) else block
    out = rpc("eth_call", [{"to": to, "data": calldata(signature, *args)}, tag])[2:]
    return [int(out[i:i + 64], 16) for i in range(0, len(out), 64)]


def call(to: str, signature: str, *args, block="latest") -> int:
    return calls([(to, signature, args)], block)[0]


def block_number() -> int:
    return int(rpc("eth_blockNumber", []), 16)


def mine(n: int) -> None:
    rpc("anvil_mine", [hex(n)])


def receipt(tx: str, timeout: float = 30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        r = rpc("eth_getTransactionReceipt", [tx])
        if r:
            return r
        time.sleep(0.2)
    raise Failed(f"no receipt for {tx}")


# Every transaction a helper program (lp2-stake, lp2-fund) reports it sent,
# in order: demo/public.py keeps them with the run's own sends.
SENT: list[str] = []


def sent_by(output: str) -> list[str]:
    """The transaction hashes a helper program's output reports, one on each
    "approved:", "sent:" or "funded:" line."""
    return [w.rstrip(")") for line in output.splitlines() if line.startswith(("approved:", "sent:", "funded:"))
            for w in line.split() if w.startswith("0x") and len(w.rstrip(")")) == 66]


def send(key: str, to: str, signature: str, *args, gas: int | None = None) -> dict:
    argv = ["cast", "send", "--json", "--rpc-url", RPC, "--private-key", key, to, signature, *map(str, args)]
    if gas:
        argv[2:2] = ["--gas-limit", str(gas)]
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0 and not done.stdout.strip().startswith("{"):
        raise Failed(f"send {signature[:40]}: {done.stderr.strip()[-400:]}")
    r = json.loads(done.stdout)
    if gas is None and r["status"] != "0x1":
        raise Failed(f"send {signature[:40]}: reverted")
    return r


# Blocks asked for in one eth_getLogs: hosted endpoints cap the range, and a
# public run spans tens of thousands of blocks (LP2_LOG_PAGE_BLOCKS overrides).
LOG_PAGE = int(os.environ.get("LP2_LOG_PAGE_BLOCKS", "5000"))


def logs(address: str, signature: str, upto: int | str = "latest") -> list[dict]:
    """The contract's logs of this event from FROM_BLOCK through `upto`, in
    chain order, read LOG_PAGE blocks at a time."""
    end = upto if isinstance(upto, int) else int(rpc("eth_getBlockByNumber", [upto, False])["number"], 16)
    pages = rpc_batch([("eth_getLogs", [{"address": address, "fromBlock": hex(a), "toBlock": hex(min(a + LOG_PAGE - 1, end)),
                                         "topics": [topic(signature)]}]) for a in range(FROM_BLOCK, end + 1, LOG_PAGE)])
    out = []
    for page in pages:
        if "error" in page:
            raise Failed(f"eth_getLogs: {page['error']}")
        out += page["result"]
    return out


class Provider:
    """A stand-in RPC provider on host:port. It forwards each request to the
    chain, and keeps the first block of each eth_getLogs it forwards in
    log_from_blocks. After reject() it answers each with 401 and `secrets`
    (a key and a password, escaped as the URL writes it) in its status line
    and its body, as a provider that rejects a key and quotes it does; after
    redirect() it answers each with a 307 to a second host (the same
    server, named by its address), which answers with a 307 whose Location
    carries `marker` and cannot be read, and counts in followed each
    request that reaches it; after refuse() it no longer listens, so each
    connection is refused. forward() makes it forward again."""

    def __init__(self, host: str, port: int, secrets: tuple[str, str] = ("", ""), marker: str = ""):
        import http.server
        import threading
        provider = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/second":
                    provider.followed += 1
                    self.send_response(307)
                    self.send_header("Location", f"http://[{marker}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if provider.mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", f"http://{host}:{port}/second")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if provider.mode == "reject":
                    said = f"invalid project id {secrets[0]} or password {secrets[1]}"
                    out = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": said}}).encode()
                    self.send_response(401, f"Unauthorized: {said}")
                else:
                    try:
                        asked = json.loads(body)
                    except ValueError:
                        asked = []
                    for one in asked if isinstance(asked, list) else [asked]:
                        if isinstance(one, dict) and one.get("method") == "eth_getLogs":
                            provider.log_from_blocks.append(int(one["params"][0]["fromBlock"], 16))
                    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        out = r.read()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        self.mode = "forward"
        self.followed = 0
        self.log_from_blocks: list[int] = []
        try:
            self.server = http.server.ThreadingHTTPServer((host, port), Handler)
        except OSError as e:
            raise NotRun(f"port {port} (the stand-in RPC provider) on {host}: {e}; set LP2_DEMO_PORT_BASE to move the demo's ports")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def forward(self) -> None:
        self.mode = "forward"

    def reject(self) -> None:
        self.mode = "reject"

    def redirect(self) -> None:
        self.mode = "redirect"

    def refuse(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# --- names, keys and records --------------------------------------------------

NAMES: dict[str, str] = {}   # lower-case address -> name used in the ledger case
ADDR: dict[str, str] = {}
KEY: dict[str, str] = {}
RECORDS: list[dict] = []


def new_key(name: str) -> None:
    key = "0x" + os.urandom(32).hex()
    addr = sh(["cast", "wallet", "address", key])
    KEY[name], ADDR[name] = key, addr
    NAMES[addr.lower()] = name
    rpc("anvil_setBalance", [addr, hex(10**20)])


def keystore(name: str, password_file: Path) -> Path:
    d = RUN / "keys" / name
    d.mkdir(parents=True)
    sh(["cast", "wallet", "import", "-k", str(d), "--private-key", KEY[name],
        "--unsafe-password", password_file.read_text(), name])
    f = d / name
    data = json.loads(f.read_text())
    data["address"] = ADDR[name][2:].lower()  # the node reads the address field
    f.write_text(json.dumps(data))
    f.chmod(0o600)
    return f


def record(fields: dict, tx_receipt: dict | None = None, block: int | None = None, **extra) -> dict:
    """A step as the demo sent it. A transaction is placed by its receipt; a
    read, or a call the contract refused, by the block it was taken at."""
    if tx_receipt is not None:
        block, index = int(tx_receipt["blockNumber"], 16), int(tx_receipt["transactionIndex"], 16)
        extra.setdefault("tx", tx_receipt["transactionHash"])
    else:
        index = None
    r = {"fields": fields, "block": block, "index": index, "seq": len(RECORDS), **extra}
    RECORDS.append(r)
    return r


# --- the node's tickets and jobs -----------------------------------------------

C: dict[str, str] = {}   # contract addresses


# The contracts whose code may be replaced at the same address (rulebook
# R-27): each sits behind a Proxy whose admin is Upgrades, on the side whose
# stop lets its code be replaced at once (IUpgrades.Side: 0 USDC, 1 LPT,
# 2 both).
UPGRADEABLE = {"RoundClock": 2, "Registry": 2, "Parameters": 2, "Broker": 0, "StakeLedger": 1, "Scores": 1,
               "Claims": 1, "Buyback": 1}
SIDES = ("USDC", "LPT", "both")
IMPL: dict[str, str] = {}   # the code each proxy was created with
# ERC-1967's slots: the code in force, and the proxy's admin.
IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"

SOURCES = {
    "RoundClock": "src/clock/RoundClock.sol:RoundClock", "MockUSDC": "src/money/MockUSDC.sol:MockUSDC",
    "Registry": "src/authority/Registry.sol:Registry", "Broker": "src/money/Broker.sol:Broker",
    "Controller": "src/authority/Controller.sol:Controller", "TestLPT": "src/money/TestLPT.sol:TestLPT",
    "StakeLedger": "src/money/StakeLedger.sol:StakeLedger", "Scores": "src/scores/Scores.sol:Scores",
    "TestPool": "src/money/TestPool.sol:TestPool", "Parameters": "src/authority/Parameters.sol:Parameters",
    "Claims": "src/money/Claims.sol:Claims", "Buyback": "src/money/Buyback.sol:Buyback",
}

# Each deployment's contracts, in the order they are created.
PAYMENTS = ["RoundClock", "MockUSDC", "Registry", "Broker", "Controller", "Parameters"]
REWARDS = ["RoundClock", "MockUSDC", "Registry", "Broker", "Controller", "TestLPT", "StakeLedger", "Scores",
           "Parameters", "Claims"]
FULL = ["RoundClock", "MockUSDC", "Registry", "Broker", "Controller", "TestLPT", "StakeLedger", "Scores", "TestPool",
        "Parameters", "Claims", "Buyback"]


def made_up(name: str) -> str:
    """An address the run makes up for `name`, whose key nobody holds."""
    if name not in ADDR:
        ADDR[name] = "0x" + os.urandom(20).hex()
        NAMES[ADDR[name].lower()] = name
    return ADDR[name]


def governance() -> str:
    """Parameters' authority: G when the run made its key (./dev demo
    buyback), otherwise an address the run makes up and whose key nobody
    holds, so that nothing can be changed."""
    return made_up("G")


def upgrade_keys() -> tuple[str, str]:
    """The upgrade key U and the pause key S (R-27): addresses the run makes
    up and whose keys nobody holds, so that no code is replaced and no side
    stopped."""
    return made_up("Upgrader"), made_up("Pauser")


def constructor_args(name: str, at: dict[str, str]) -> list:
    """`name`'s wiring: the addresses everyone calls, each a proxy for an
    upgradeable contract, including those created after it; ZERO for a
    contract the deployment does not have."""
    has = lambda n: at.get(n, ZERO)  # noqa: E731
    return {
        "RoundClock": lambda: [],
        "MockUSDC": lambda: [],
        # The Registry refuses the Broker as a payout address.
        "Registry": lambda: [at["RoundClock"], at["Broker"]],
        "Broker": lambda: [at["MockUSDC"], at["Registry"], at["RoundClock"], at["Parameters"], at["Upgrades"]],
        "Controller": lambda: [at["Registry"], at["RoundClock"], at["Broker"]],
        # Claims is the token's one minter.
        "TestLPT": lambda: [at["Claims"], MANIFEST["lpt"]["genesis_supply_wei"]],
        "StakeLedger": lambda: [at["TestLPT"], at["Registry"], at["RoundClock"], at["Parameters"], at["Upgrades"]],
        "Scores": lambda: [at["Registry"], at["RoundClock"], at["StakeLedger"], at["Parameters"]],
        "TestPool": lambda: [at["MockUSDC"], at["TestLPT"]],
        # The score source, the pool and the buyback executor (group 10):
        # the Buyback, or none, so that nobody may draw.
        "Parameters": lambda: [governance(), at["RoundClock"], has("Scores"), has("TestPool"), has("Buyback")],
        "Claims": lambda: [at["TestLPT"], at["Registry"], at["RoundClock"], at["Broker"], at["StakeLedger"],
                           at["Parameters"], at["Scores"], ADDR["Treasury"], at["Upgrades"]],
        # Pinned to the runtime code of the pool deployed before it.
        "Buyback": lambda: [at["Parameters"], at["Broker"], at["RoundClock"], at["TestLPT"], at["MockUSDC"],
                            sh(["cast", "keccak", rpc("eth_getCode", [C["TestPool"], "latest"])]), at["Upgrades"]],
    }[name]()


def deploy_system(parts: list[str]) -> None:
    """Every contract of `parts`, in order, then Upgrades (rulebook R-27).
    An upgradeable contract is its code, then a Proxy running it, with
    Upgrades' predicted address as admin (and, for RoundClock, its
    initialize); the wiring, the Controller's names, TestLPT's minter and
    Parameters' executor name proxies, never code. Every address is
    predicted from the deployer's nonce before the first creation, and each
    must land where it was predicted."""
    nonce = int(rpc("eth_getTransactionCount", [ADDR["deployer"], "latest"]), 16)
    at, k = {}, 0
    for name in parts:
        k += 1 if name in UPGRADEABLE else 0
        at[name] = compute_address(nonce + k)
        k += 1
    at["Upgrades"] = compute_address(nonce + k)
    upgrader, pauser = upgrade_keys()
    for name in parts:
        args = constructor_args(name, at)
        if name in UPGRADEABLE:
            IMPL[name] = forge_create(SOURCES[name], *args)
            init = calldata("initialize()") if name == "RoundClock" else "0x"
            C[name] = forge_create("src/authority/Proxy.sol:Proxy", IMPL[name], at["Upgrades"], init)
        else:
            C[name] = forge_create(SOURCES[name], *args)
        if C[name].lower() != at[name].lower():
            raise Failed(f"{name} landed at {C[name]}, not {at[name]} as the deployment predicted")
    proxies = [n for n in parts if n in UPGRADEABLE]
    C["Upgrades"] = forge_create("src/authority/Upgrades.sol:Upgrades", C["RoundClock"], upgrader, pauser,
                                 "[" + ",".join(C[n] for n in proxies) + "]",
                                 "[" + ",".join(str(UPGRADEABLE[n]) for n in proxies) + "]")
    if C["Upgrades"].lower() != at["Upgrades"].lower():
        raise Failed(f"Upgrades landed at {C['Upgrades']}, not {at['Upgrades']}, which every proxy names as its admin")
    check_deployment(proxies, upgrader, pauser)
    for n in ("Claims", "StakeLedger"):
        if n in C:
            NAMES[C[n].lower()] = n
            ADDR[n] = C[n]


def check_deployment(proxies: list[str], upgrader: str, pauser: str) -> None:
    """The Controller's three names; each proxy's ERC-1967 admin slot is
    Upgrades and its implementation slot the code deployed for it, on its
    side; Upgrades reads the round through the clock's proxy; three distinct
    keys."""
    names = {"BondingManager": "Registry", "RoundsManager": "RoundClock", "TicketBroker": "Broker"}
    for legacy, ours in names.items():
        got = call(C["Controller"], "getContract(bytes32)", sh(["cast", "keccak", legacy]))
        if got != int(C[ours], 16):
            raise Failed(f"the Controller does not answer {legacy} with the {ours}")
    for n in proxies:
        admin, code = (int(rpc("eth_getStorageAt", [C[n], s, "latest"]), 16) for s in (ADMIN_SLOT, IMPLEMENTATION_SLOT))
        if (admin, code) != (int(C["Upgrades"], 16), int(IMPL[n], 16)):
            raise Failed(f"{n}'s proxy names admin {admin:#x} and code {code:#x}, not Upgrades and the code deployed")
        if call(C["Upgrades"], "sideOf(address)", C[n]) != UPGRADEABLE[n]:
            raise Failed(f"Upgrades does not put {n} on the {SIDES[UPGRADEABLE[n]]} side")
    keys = [int(k, 16) for k in (governance(), upgrader, pauser)]
    if len(set(keys)) != 3:
        raise Failed("the authority, the upgrade key and the pause key are not three distinct addresses")
    got = [call(C["Upgrades"], f) for f in ("clock()", "upgrader()", "pauser()")]
    if got != [int(C["RoundClock"], 16), keys[1], keys[2]]:
        raise Failed("Upgrades does not read the clock's proxy, or does not hold the deployment's two keys")


def deploy_payments() -> None:
    """The payments contracts. The Broker reads its fee split, and the one
    executor that may draw its network share (group 10), from Parameters,
    which names none: nobody may draw."""
    deploy_system(PAYMENTS)


def deploy_rewards() -> None:
    """Payments's contracts with test LPT, StakeLedger, Scores and Claims.
    The token's one minter is Claims; Parameters names Scores as the score
    source and no executor."""
    deploy_system(REWARDS)


# How forge signs as the deployer: the demo's throwaway key, or, when set
# (demo/public.py), a keystore and its password file.
DEPLOYER_SIGNER: list[str] = []
CREATED: list[str] = []   # the hash of every contract creation, in order


def forge_create(path: str, *args) -> str:
    signer = DEPLOYER_SIGNER or ["--private-key", KEY["deployer"]]
    argv = ["forge", "create", "--root", str(ROOT / "contracts"), "--rpc-url", RPC,
            *signer, "--broadcast", path]
    if args:
        argv += ["--constructor-args", *map(str, args)]
    out = sh(argv)
    CREATED.extend(line.split()[-1] for line in out.splitlines() if line.startswith("Transaction hash:"))
    return next(line.split()[-1] for line in out.splitlines() if line.startswith("Deployed to:"))


def compute_address(nonce: int) -> str:
    """The address of the deployer's contract creation at `nonce`."""
    return sh(["cast", "compute-address", ADDR["deployer"], "--nonce", str(nonce)]).split()[-1]


def current_round(block="latest") -> int:
    return call(C["RoundClock"], "currentRound()", block=block)


def round_started_at() -> int:
    found = logs(C["RoundClock"], "RoundStarted(uint256,uint256,bytes32)")
    return int(found[-1]["data"][2:66], 16)


def settlements() -> list[dict]:
    """Every ticket the Broker settled, with what the node sent to redeem it."""
    out = []
    for log in logs(C["Broker"], "FeeSettled(uint256,address,address,bytes32,uint256,uint256,uint256,address)"):
        d = log["data"][2:]
        word = lambda i: int(d[64 * i:64 * (i + 1)], 16)  # noqa: E731
        tx = rpc("eth_getTransactionByHash", [log["transactionHash"]])
        ticket = decode_ticket(tx["input"])
        out.append({"round": int(log["topics"][1], 16), "node": "0x" + log["topics"][2][-40:],
                    "payer": "0x" + log["topics"][3][-40:], "hash": "0x" + d[0:64], "gross": word(1),
                    "node_amount": word(2), "network_amount": word(3), "block": int(log["blockNumber"], 16),
                    "index": int(log["transactionIndex"], 16), "tx": log["transactionHash"], "from": tx["from"],
                    "input": tx["input"], **ticket})
    return out


def decode_ticket(data: str) -> dict:
    """redeemWinningTicket((recipient, sender, face, winProb, nonce, randHash, auxData), sig, rand)."""
    if data[:10] != sel("redeemWinningTicket((address,address,uint256,uint256,uint256,bytes32,bytes),bytes,uint256)"):
        raise Failed("a settled ticket was not redeemed by redeemWinningTicket")
    b = bytes.fromhex(data[10:])
    word = lambda at: int.from_bytes(b[at:at + 32], "big")  # noqa: E731
    t = word(0)
    aux_at = t + word(t + 6 * 32)
    aux_len = word(aux_at)
    if aux_len != 64:
        raise Failed("a settled ticket's auxData is not 64 bytes")
    return {"recipient": "0x" + b[t + 12:t + 32].hex(), "sender": "0x" + b[t + 44:t + 64].hex(),
            "face": word(t + 64), "created_round": word(aux_at + 32)}


def name(addr: str) -> str:
    return NAMES[addr.lower()]


def wait_settled(count: int, why: str) -> list[dict]:
    """Mine past the tickets' parameter expiry, then wait for the node to redeem."""
    mine(41)
    end = time.time() + 120
    while time.time() < end:
        s = settlements()
        if len(s) > count:
            raise Failed(f"{why}: the Broker settled {len(s)} tickets, want {count}")
        if len(s) == count:
            return s
        time.sleep(1)
    raise Failed(f"{why}: the nodes redeemed {len(settlements())} tickets, want {count} (see .cache/demo/run/logs/)")


def post(url: str, body: dict, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except (urllib.error.URLError, ConnectionError) as e:
        return 0, str(e)


JOB_PATH = "/apps/llm/app/v1/chat/completions"
JOB = {"messages": [{"role": "user", "content": "Name one colour of the sky."}], "max_tokens": 16}


def challenge(payer: str, orch: int) -> tuple[int, str]:
    return post(f"http://127.0.0.1:{orch}" + JOB_PATH, JOB, {"Livepeer-Payer-Address": ADDR[payer]})


def sign(signer: int, body: str) -> tuple[int, str]:
    return post(f"http://127.0.0.1:{signer}/generate-live-payment",
                {"orchestrator": json.loads(body)["payment_params"], "type": "fixed"})


def pay(payer: str, signed: str, orch: int) -> tuple[int, str]:
    s = json.loads(signed)
    return post(f"http://127.0.0.1:{orch}" + JOB_PATH, JOB, {"Livepeer-Payer-Address": ADDR[payer],
                                                             "Livepeer-Payment": s["payment"], "Livepeer-Segment": s["segCreds"]})


def job(payer: str, orch: int, signer: int) -> str:
    code, body = challenge(payer, orch)
    if code != 402:
        raise Failed(f"a job's first request: got HTTP {code} {body[:200]}, want 402 with a payment challenge")
    code, signed = sign(signer, body)
    if code != 200:
        raise Failed(f"the payer's signer: got HTTP {code} {signed[:200]}")
    code, answer = pay(payer, signed, orch)
    if code != 200:
        raise Failed(f"the paid request: got HTTP {code} {answer[:200]}")
    return json.loads(answer)["choices"][0]["message"]["content"].strip().replace("\n", " ")


def fund(payer: str, deposit: int, earmark: int, node: str | None) -> None:
    send(KEY["deployer"], C["MockUSDC"], "mint(address,uint256)", ADDR[payer], deposit + earmark)
    argv = [str(RUN / "bin" / "lp2-fund"), "--rpc-url", RPC, "--keystore-file", str(RUN / "keys" / payer / payer),
            "--keystore-password-file", str(RUN / "pw"), "--controller-address", C["Controller"],
            "--usdc-address", C["MockUSDC"], "--deposit", str(deposit), "--earmark", str(earmark)]
    if node:
        argv += ["--node", ADDR[node]]
    out = sh(argv)
    tx = next(w.rstrip(")") for line in out.splitlines() if line.startswith("funded:") for w in line.split() if w.startswith("0x") and len(w.rstrip(")")) == 66)
    fields = {"action": "fund", "payer": payer, "deposit_micro_usdc": deposit}
    if node:
        fields["node"] = node
    fields["earmark_micro_usdc"] = earmark
    record(fields, receipt(tx))
    if call(C["MockUSDC"], "balanceOf(address)", ADDR[payer]) != 0:
        raise Failed(f"{payer}'s wallet is not empty after funding, so it cannot stand for what was released")


def observe(payer: str, node: str) -> tuple[int, int]:
    """What the node is shown: the two reads it makes before it accepts a payer."""
    b = block_number()
    deposit = call(C["Broker"], "getSenderInfo(address)", ADDR[payer], block=b)
    claimable = call(C["Broker"], "claimableReserve(address,address)", ADDR[payer], ADDR[node], block=b)
    record({"action": "observe", "payer": payer, "node": node}, block=b, shown=(deposit, claimable))
    return deposit, claimable


def request_release(payer: str, node: str | None, amount: int) -> dict:
    r = send(KEY[payer], C["Broker"], "requestRelease(address,uint256)", ADDR[node] if node else ZERO, amount)
    fields = {"action": "request_release", "payer": payer}
    if node:
        fields["node"] = node
    fields["amount_micro_usdc"] = amount
    record(fields, r)
    return r


def release(payer: str, node: str | None) -> dict:
    r = send(KEY[payer], C["Broker"], "release(address)", ADDR[node] if node else ZERO)
    fields = {"action": "release", "payer": payer}
    if node:
        fields["node"] = node
    record(fields, r)
    return r


# --- reconciling against the calculator ---------------------------------------

def assemble(extra: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Every step so far, in chain order: what the demo sent, each ticket a
    node redeemed (its face and creation round read from the redemption
    transaction) and `extra`, steps another program sent that the chain
    shows. Then every view the case needs, read from the chain."""
    records = list(RECORDS) + list(extra or [])
    tickets: dict[str, str] = {}
    for s in settlements():
        if s["from"].lower() != s["recipient"].lower():
            raise Failed(f"ticket {s['hash']} was redeemed by {s['from']}, not the node it pays")
        tid = tickets.setdefault(s["hash"], f"t{len(tickets) + 1}")
        records.append({"fields": {"action": "redeem", "ticket": tid, "payer": name(s["sender"]), "node": name(s["recipient"]),
                                   "created_round": s["created_round"], "face_micro_usdc": s["face"]},
                        "block": s["block"], "index": s["index"], "seq": 10**9 + s["block"], "settled": s})
    for r in records:  # a replayed ticket keeps the id of its first redemption
        if r["fields"].get("ticket", "").startswith("0x"):
            r["fields"]["ticket"] = tickets[r["fields"]["ticket"]]
    set_rounds(records)
    for r in records:
        if "settled" in r and r["settled"]["round"] != r["round"]:
            raise Failed("a FeeSettled round differs from the clock's round at its block")
    ordered = ledgercase.order(records)
    case_steps = ledgercase.steps(ordered)
    need = ledgercase.views_needed(case_steps)
    return ordered, read_views(need, block_number(), case_steps)


def set_rounds(records: list[dict]) -> None:
    """Give each record the clock's round at its place on the chain."""
    places = sorted({(r["block"], r["index"]) for r in records}, key=place_key)
    rounds = dict(zip(places, round_batch(places)))
    for r in records:
        r["round"] = rounds[(r["block"], r["index"])]


def place_key(place: tuple[int, int | None]) -> tuple[int, int]:
    """A transaction is at (block, index); a read (index None) after every
    transaction of its block."""
    return place[0], place[1] if place[1] is not None else 1 << 62


def round_batch(places: list[tuple[int, int | None]]) -> list[int]:
    """The clock's round at each place, read from its RoundStarted logs: the
    last round started at or before it. A transaction at index i sees only
    the rounds started by transactions 0 to i of its block; a read (index
    None) sees the round after the whole block. The same answer as
    currentRound() there, without the past state a pruned node no longer
    keeps."""
    starts = sorted((int(lg["blockNumber"], 16), int(lg["transactionIndex"], 16), int(lg["topics"][1], 16))
                    for lg in logs(C["RoundClock"], "RoundStarted(uint256,uint256,bytes32)"))
    out = []
    for place in places:
        before = [r for at, i, r in starts if (at, i) <= place_key(place)]
        if not before:
            raise Failed(f"block {place[0]} is before the round clock was deployed")
        out.append(before[-1])
    return out


def read_views(need: dict, block: int, case_steps: list[dict]) -> dict:
    by_name = {v: k for k, v in NAMES.items()}
    a = by_name.__getitem__
    items = [(C["Broker"], "earmarkOf(address,address)", (a(p), ZERO)) for p in need["payers"]]
    items += [(C["Broker"], "earmarkOf(address,address)", (a(p), a(n))) for p, n in need["earmarks"]]
    wallets = sorted(set(need["wallets"]) | set(need["payers"]))
    items += [(C["MockUSDC"], "balanceOf(address)", (a(w),)) for w in wallets]
    items += [(C["Broker"], "networkHeld()", ())]
    items += [(C["Broker"], "feesOf(uint256,address)", (int(r), a(n))) for r, n in need["fees"]]
    fee_rounds = sorted({r for r, _ in need["fees"]}, key=int)
    items += [(C["Broker"], "totalFees(uint256)", (int(r),)) for r in fee_rounds]
    got = iter(calls(items, block))
    views = {"deposit": {p: next(got) for p in need["payers"]},
             "earmark": {(p, n): next(got) for p, n in need["earmarks"]},
             "wallet": {w: next(got) for w in wallets}, "network": next(got),
             "fees": {(r, n): next(got) for r, n in need["fees"]},
             "total_fees": {r: next(got) for r in fee_rounds},
             "need": need, "block": block}
    if need.get("lpt"):
        views.update(read_lpt_views(need, block, case_steps))
    return views


def read_lpt_views(need: dict, block: int, case_steps: list[dict]) -> dict:
    """Test LPT held by each name; each round's two share amounts as minted
    (RoundSharesMinted); what each claim minted (entitlementOf), which with
    the shares is what was minted for each earning round; and all LPT
    minted, the token's supply less its genesis supply."""
    by_name = {v: k for k, v in NAMES.items()}
    lpt_held = dict(zip(need["lpt"], calls([(C["TestLPT"], "balanceOf(address)", (by_name[n],)) for n in need["lpt"]], block)))
    shares = {}
    for log in logs(C["Claims"], "RoundSharesMinted(uint256,uint256,uint256)", block):
        d = log["data"][2:]
        shares[int(log["topics"][1], 16)] = (int(d[0:64], 16), int(d[64:128], 16))
    claimed = sorted({(int(s["earned_round"]), s["node"]) for s in case_steps if s["action"] == "claim"})
    paid = [words(C["Claims"], "entitlementOf(address,uint256)", by_name[n], r, block=block)[1] for r, n in claimed]
    minted: dict[int, int] = {}
    for (r, _), p in zip(claimed, paid):
        minted[r] = minted.get(r, 0) + p
    for r, (v, t) in shares.items():
        minted[r] = minted.get(r, 0) + v + t
    supply = call(C["TestLPT"], "totalSupply()", block=block)
    return {"lpt": lpt_held, "shares": shares, "minted_for_round": {r: v for r, v in minted.items() if v > 0},
            "lpt_minted": supply - int(MANIFEST["lpt"]["genesis_supply_wei"])}


def calculator(case_steps: list[dict], members: dict | None = None) -> dict:
    from reference import Parameters, calculate
    inputs = {"steps": case_steps, **({"members": members} if members else {})}
    return ledgercase.strings(calculate("ledger", inputs, Parameters.from_strings(PARAMS), {}))


ROW_NAMES = {"deposits_micro_usdc": "deposit", "earmarks_micro_usdc": "earmark", "paid_micro_usdc": "wallet",
             "released_micro_usdc": "released to", "network_micro_usdc": "network share held (networkHeld)",
             "fees_micro_usdc": "fees in round", "total_fees_micro_usdc": "all fees in round",
             "lpt_wei": "LPT of", "shares_wei": "shares", "minted_for_round_wei": "LPT minted for round",
             "lpt_minted_wei": "all LPT minted (supply less genesis)"}
FAILURES: list[str] = []


def row_order(path: tuple) -> list:
    """Figures in the case's order, numbers as numbers, deposit before earmark."""
    rank = list(ROW_NAMES) + ["observed", "refused"]
    rest = [p.replace("deposit_micro_usdc", "0").replace("claimable_micro_usdc", "1") for p in map(str, path[1:])]
    return [rank.index(path[0])] + [[int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p)] for p in rest]


def label_of(path: tuple) -> str:
    if path[0] == "observed":
        if path[2].endswith("stake_wei"):
            return f"stake shown, {path[1]}: {'stake' if path[2] == 'stake_wei' else 'total stake'}"
        return f"node shown, {path[1]}: {'deposit' if path[2].startswith('deposit') else 'earmark'}"
    if path[0] == "refused":
        return f"refused, {path[1]}"
    if path[0] == "fees_micro_usdc":
        return f"fees of {path[2]} in round {path[1]}"
    if path[0] == "total_fees_micro_usdc":
        return f"all fees in round {path[1]}"
    if path[0] == "earmarks_micro_usdc":
        return f"earmark of {path[1]} for {path[2]}" if len(path) > 2 else f"earmarks of {path[1]}"
    if path[0] in ("network_micro_usdc", "lpt_minted_wei"):
        return ROW_NAMES[path[0]]
    if path[0] == "shares_wei":
        return f"round {path[1]} {'validator' if path[2].startswith('validators') else 'treasury'} amount"
    return f"{ROW_NAMES[path[0]]} {path[1]}"


def is_lpt(path: tuple) -> bool:
    return path[0] in ("lpt_wei", "shares_wei", "minted_for_round_wei", "lpt_minted_wei") or \
        (path[0] == "observed" and path[-1].endswith("stake_wei"))


def reconcile(title: str, extra: list[dict] | None = None, members: dict | None = None,
              moved: dict | None = None) -> tuple[list[dict], dict, dict]:
    """Print every figure read from the chain beside the calculator's figure
    for the same steps; record a failure for any that differ.

    `moved` is what a buyback moved where no ledger step can follow it
    (demo/public.py's full system run): the network share its buys spent
    (`spent_micro_usdc`) and the LPT they burned (`burned_wei`), as their
    Bought events give them, and the mock USDC minted to seed its pool
    (`seeded_micro_usdc`). The first two are added back to the chain's
    network share and LPT minted before they are compared, and the third is
    counted with what the demo minted; a buyback case reconciles the buys
    themselves."""
    ordered, views = assemble(extra)
    if moved:
        views["network"] += moved["spent_micro_usdc"]
        views["lpt_minted"] += moved["burned_wei"]
    case_steps = ledgercase.steps(ordered)
    chain = ledgercase.expected(ordered, views["need"], views)
    calc = calculator(case_steps, members)
    flat_chain, flat_calc = ledgercase.flatten(chain), ledgercase.flatten(calc)
    w = 30 if views["need"].get("lpt") else 14
    say(f"\n  {title}: {len(case_steps)} steps, chain at block {views['block']}, round {current_round()}")
    say(f"  {'figure (micro-USDC' + ('; LPT' if w > 14 else '') + ')':<46}{'chain':>{w}}{'calculator':>{w}}")
    quiet = 0
    for path in sorted(set(flat_chain) | set(flat_calc), key=row_order):
        got, want = flat_chain.get(path, "absent"), flat_calc.get(path, "absent")
        if path[0] in ("observed", "refused") and path[-1] == "steps":
            continue
        if got == want and (got in ("0", "{}") and str(path[1]).startswith("J")):
            quiet += 1
            continue
        mark = "equal" if got == want else "DIFFERS"
        fmt = (lambda v: lpt(v) if str(v).isdigit() else str(v)) if is_lpt(path) else \
              (lambda v: money(v) if str(v).isdigit() else str(v))
        say(f"  {label_of(path):<46}{fmt(got):>{w}}{fmt(want):>{w}}  {mark}")
        if got != want:
            FAILURES.append(f"{title}: {label_of(path)}: chain {got}, calculator {want}")
    if quiet:
        say(f"  {'wallets of ' + str(quiet) + ' junk nodes, each':<46}{'0':>{w}}{'0':>{w}}  equal")
    if moved:
        say(f"  (the chain's network share held is shown with the {money(moved['spent_micro_usdc'])} micro-USDC the buys "
            f"spent added back, and all LPT minted with the {lpt(moved['burned_wei'])} LPT they burned)")
    custody(title, case_steps, calc, views["block"], w, moved)
    diffs = ledgercase.differences(chain, calc)
    if diffs:
        say(f"  {len(diffs)} figure(s) differ")
    else:
        say(f"  every figure equal ({len(flat_chain)} figures)")
    return ordered, views, calc


def custody(title: str, case_steps: list[dict], calc: dict, block: int, w: int = 14, moved: dict | None = None) -> None:
    """The Broker's own USDC, beside what the calculator says it holds: every
    deposit, earmark and network share. And all the USDC there is, beside
    what the demo minted to payers to fund them, so no USDC was made or kept
    anywhere the figures above do not look. With `moved` (see reconcile),
    the buys' spend is added back to the Broker's USDC, and the pool's seed
    is counted as minted by the demo."""
    broker, supply = calls([(C["MockUSDC"], "balanceOf(address)", (C["Broker"],)),
                            (C["MockUSDC"], "totalSupply()", ())], block)
    held = (sum(int(v) for v in calc["deposits_micro_usdc"].values())
            + sum(int(v) for e in calc["earmarks_micro_usdc"].values() for v in e.values())
            + int(calc["network_micro_usdc"]))
    minted = sum(int(s["deposit_micro_usdc"]) + int(s["earmark_micro_usdc"]) for s in case_steps if s["action"] == "fund")
    if moved:
        broker += moved["spent_micro_usdc"]
        minted += moved["seeded_micro_usdc"]
    for label, got, want, source in (("USDC the Broker holds", broker, held, "calculator"),
                                     ("all USDC in existence", supply, minted, "demo minted")):
        mark = "equal" if got == want else "DIFFERS"
        say(f"  {label:<46}{money(got):>{w}}{money(want):>{w}}  {mark} ({source})")
        if got != want:
            FAILURES.append(f"{title}: {label}: chain {got}, {source} {want}")


def check(what: str, got, want) -> None:
    ok = got == want
    say(f"  {'ok  ' if ok else 'FAIL'} {what}: {got}" + ("" if ok else f", want {want}"))
    if not ok:
        FAILURES.append(f"{what}: got {got}, want {want}")


def scan(ordered: list[dict], targets: list[str], known_extra: set[str] = frozenset()) -> None:
    """Every transaction to the named contracts is a step of the case."""
    known = {r["tx"].lower() for r in RECORDS if r.get("tx")} | {r["settled"]["tx"].lower() for r in ordered if "settled" in r}
    known |= {r["tx"].lower() for r in ordered if r.get("tx")} | {t.lower() for t in known_extra}
    blocks = rpc_batch([("eth_getBlockByNumber", [hex(b), True]) for b in range(FROM_BLOCK, block_number() + 1)])
    to = {C[t].lower() for t in targets}
    stray = [t["hash"] for b in blocks for t in b["result"]["transactions"]
             if (t.get("to") or "").lower() in to and t["hash"].lower() not in known]
    check(f"every transaction to the {' or the '.join(targets)} is a step of the case", stray, [])


def write_case(case_id: str, ordered: list[dict], views: dict, members: dict | None = None) -> None:
    """The run as a ledger case, checked by ./calculator/check."""
    the_case = ledgercase.case(case_id, ordered, views["need"], views, members)
    out = RUN / "ledger-case.json"
    out.write_text(json.dumps(ledgercase.document(VECTORS, [the_case]), indent=1) + "\n")
    done = subprocess.run([str(ROOT / "calculator" / "check"), str(out)], capture_output=True, text=True)
    say(f"  wrote {where(out)}: {len(the_case['inputs']['steps'])} steps, as sent and as the chain shows them")
    for line in done.stdout.strip().splitlines()[-2:]:
        say(f"  ./calculator/check {where(out)}: {line[:150]}{' ...' if len(line) > 150 else ''}")
    check("the calculator reproduces the run's case", done.returncode, 0)


def run_main(main) -> None:
    """Run a stage's main with the demo's exit codes, and always clean up."""
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    code = 2
    try:
        code = main()
    except NotRun as e:
        say(f"DEMO NOT RUN: {e}")
        code = 2
    except Failed as e:
        say(f"DEMO FAILED: {e}")
        code = 1
    except KeyboardInterrupt:
        say("DEMO interrupted")
        code = 2
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl-C must not stop the cleanup
        cleanup()
    sys.exit(code)


def finish(started: float, passed: str) -> int:
    say(f"\nTook {time.time() - started:.0f} s. Logs, addresses and the case are in {where(RUN)}/.")
    if FAILURES:
        say(f"DEMO FAILED: {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            say(f"  {f}")
        return 1
    say(f"DEMO PASSED: {passed}")
    return 0
