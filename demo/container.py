"""./dev demo container: one operator container joins the standing testnet's
services on a local chain, with nobody acting after it is funded.

Deploys stage 4's contracts on a local chain that mines a block a second,
and runs the services as ./dev demo light runs them: the keeper, the payer
P's signer, lp2-sender (with --allow-private-urls, since the node's URL is
on loopback, and minting only the mock USDC it funds) and lp2-faucet,
granting each registered node 1,000 test LPT. Builds the operator image
from programs/container and runs it on one data folder. The demo reads the
node key N and the operator key H the container prints, sends each 1 ETH,
and from then on only watches, kills and restarts it:

  1. killed while it waits for the faucet's grant (the faucet is started
     only after this kill, so the wait is certain);
  2. killed between H's approval and H's bond (LP2_TEST_HOLD=approved);
  3. a second container on the same data folder refuses to start;
  4. killed between a claim and its withdrawal (LP2_TEST_HOLD=claimed);
  5. restarted, it withdraws H's part;
  6. a container whose RPC provider cannot be reached, and one whose
     provider rejects the request and echoes the key and the Basic-auth
     password in its URL (escaped, as the URL writes it), each name the
     step, the provider's host and the failure's category in their log,
     and no credential; started again with public port 09000 (9000, where
     the responder listened before, written with a leading zero) and that
     RPC URL, its node comes up beside the responder (LP2_TEST_HOLD=started);
     then its provider rejects and echoes, and then refuses connections,
     and the explorer tells each by its category, and neither credential,
     as written or decoded, appears in any of the container's Docker
     output, the files under its logs folder, or the explorer's page and
     JSON.

Then the chain is read: no second registration, grant, bond or claim; N's
key sent nothing after the node started but its own redemptions; N was
paid exactly once in every round it was up for whole; and the run, every
step of it as the chain shows it, is written as a ledger case the
calculator reproduces, against which the container's explorer's figures
for the claimed round are checked.

Exit 0 every check passed; 1 one failed; 2 the run could not run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import common
import light
import stage3 as s3
import buyback as bb
import ledgercase
common.use("stage-4-local.json")
from common import *  # noqa: E402,F401,F403
from common import PORT, C, ADDR, KEY, NAMES, BASE, MANIFEST, PARAMS, ROOT  # noqa: E402

IMAGE = f"lp2-node:demo-{BASE}"
DATA = common.RUN / "container-data"
DEPLOYMENT = common.RUN / "deployment.json"
PUBLIC = f"http://127.0.0.1:{PORT['orch']}"
RUN_LIMIT = 20 * 60          # the most the demo waits for the first claim, in seconds
FUNDED_WEI = 10**18          # what the demo sends N and H each
STARTED: dict[str, int] = {}  # the block the chain was at when the container said it starts the node
ROUNDS: dict[str, int] = {}   # the round when N joined, was killed, was up again and was stopped
SYNTHETIC_KEY = "synthetic-provider-key"
SYNTHETIC_PASSWORD = "synthetic%40provider-password"  # a Basic-auth password, escaped as a URL writes it
CREDENTIALS = (SYNTHETIC_KEY, SYNTHETIC_PASSWORD, urllib.parse.unquote(SYNTHETIC_PASSWORD))
PROVIDER = BASE + 30         # the stand-in RPC provider whose URL carries the credentials
KEYED = (f"http://synthetic-user:{SYNTHETIC_PASSWORD}@host.docker.internal:{PROVIDER}"
         f"/v3/{SYNTHETIC_KEY}?apikey={SYNTHETIC_KEY}")


class Provider:
    """A stand-in RPC provider, on Docker's bridge, whose URL carries
    SYNTHETIC_KEY and SYNTHETIC_PASSWORD. It forwards each request to the
    chain; after reject() it answers each with 401 and the key and the
    password, escaped as the URL writes it, in its status line and its
    body, as a provider that rejects a key and quotes it does; after
    refuse() it no longer listens, so each connection is refused."""

    def __init__(self, host: str):
        import http.server
        import threading
        provider = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if provider.rejecting:
                    said = f"invalid project id {SYNTHETIC_KEY} or password {SYNTHETIC_PASSWORD}"
                    out = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": said}}).encode()
                    self.send_response(401, f"Unauthorized: {said}")
                else:
                    req = urllib.request.Request(common.RPC, data=body, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        out = r.read()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        self.rejecting = False
        try:
            self.server = http.server.ThreadingHTTPServer((host, PROVIDER), Handler)
        except OSError as e:
            raise NotRun(f"port {PROVIDER} (the stand-in RPC provider) on {host}: {e}; set LP2_DEMO_PORT_BASE to move the demo's ports")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reject(self) -> None:
        self.rejecting = True

    def refuse(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def gateway() -> str:
    """The address of the host on Docker's default bridge, where the chain
    and the sender listen for the container."""
    out = sh(["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"])
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", out):
        raise NotRun(f"Docker's bridge has no IPv4 gateway ({out!r})")
    return out


def start_chain(host: str) -> None:
    """anvil, a block a second, on loopback for the services and on the
    bridge for the container."""
    chain = MANIFEST["chain"]
    start("anvil", ["anvil", "--port", str(PORT["chain"]), "--chain-id", str(chain["chain_id"]),
                    "--block-time", str(chain["block_time_seconds"]), "--slots-in-an-epoch", "1",
                    "--host", "127.0.0.1", "--host", host])
    for _ in range(100):
        try:
            block_number()
            common.RPC_FILE.write_text(common.RPC)
            return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.1)
    raise NotRun("the local chain did not start")


def build_image() -> None:
    node = MANIFEST["node"]
    pinned = re.search(r"^ARG NODE_COMMIT=(\w+)$", (ROOT / "programs" / "container" / "Dockerfile").read_text(), re.M)
    check("the Dockerfile builds the node at the manifest's commit", pinned and pinned.group(1), node["commit"])
    src = common.CACHE / f"node-{node['commit'][:12]}" / "src"
    if not (src / ".git").is_dir():
        raise NotRun(f"the node's source is not at {where(src)}")
    say("  building the operator image (the first build takes a few minutes)")
    done = subprocess.run(["docker", "build", "-q", "-t", IMAGE, "-f", str(ROOT / "programs" / "container" / "Dockerfile"),
                           "--build-context", f"node={src}", str(ROOT / "programs")], capture_output=True, text=True)
    if done.returncode != 0:
        raise NotRun(f"could not build the image: {done.stderr.strip()[-600:]}")


def write_deployment(first_block: int) -> None:
    """The operator-facing deployment file the container reads."""
    price = MANIFEST["price"]
    DEPLOYMENT.write_text(json.dumps({
        "testnet": f"local-{MANIFEST['chain']['chain_id']}",
        "chain_id": MANIFEST["chain"]["chain_id"],
        "first_block": first_block,
        "contracts": {k: C[k] for k in ("Controller", "Parameters", "StakeLedger", "Claims", "TestLPT", "MockUSDC")},
        "node": {"job_price_usd": price["job_price_usd"], "price_unit": price["unit"], "wei_per_usd": price["wei_per_usd"],
                 "ticket_face_value": str(ticket_face()), "ticket_win_prob": price["ticket_win_prob"]},
        "sender_url": f"http://host.docker.internal:{light.SENDER}",
    }, indent=1) + "\n")
    DEPLOYMENT.chmod(0o644)


# --- the container ---------------------------------------------------------------

RUNS: list[str] = []


def container_argv(name: str, hold: str | None, ports: tuple[str, ...], public: str = PUBLIC,
                   rpc: str = f"http://host.docker.internal:{PORT['chain']}", data=DATA) -> list[str]:
    argv = ["docker", "run", "--name", name, "--user", f"{os.getuid()}:{os.getgid()}",
            "--add-host", "host.docker.internal:host-gateway",
            "-v", f"{data}:/data", "-v", f"{DEPLOYMENT}:/deployment.json:ro",
            "-e", "LP2_DEPLOYMENT=/deployment.json", "-e", f"LP2_RPC_URL={rpc}",
            "-e", f"LP2_PUBLIC_URL={public}", "-e", "LP2_POLL=2"]
    if "orch" in ports:
        argv += ["-p", f"127.0.0.1:{PORT['orch']}:{PORT['orch']}"]
    if "explorer" in ports:
        argv += ["-p", f"127.0.0.1:{PORT['explorer']}:8080"]
    if hold:
        argv += ["-e", f"LP2_TEST_HOLD={hold}"]
    return argv + [IMAGE]


def run_node(hold: str | None = None, ports: tuple[str, ...] = ("orch", "explorer"), **how) -> str:
    name = f"lp2-demo-node-{BASE}-{len(RUNS) + 1}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    common.CONTAINERS.append(name)
    argv = container_argv(name, hold, ports, **how)
    argv.insert(2, "-d")
    sh(argv)
    RUNS.append(name)
    say(f"  container {len(RUNS)} started" + (f", with LP2_TEST_HOLD={hold}" if hold else ""))
    return name


def clog(name: str) -> str:
    done = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    return done.stdout + done.stderr


def running(name: str) -> bool:
    return sh(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False) == "true"


def wait_log(name: str, text: str, seconds: float) -> str:
    """Wait for the container's first log line holding text; fail if it stops first."""
    end = time.time() + seconds
    while time.time() < end:
        for line in clog(name).splitlines():
            if text in line:
                return line
        if not running(name):
            break
        time.sleep(1)
    keep(name)
    last = clog(name).strip().splitlines()[-3:]
    raise Failed(f"{name}: no log line with {text!r} within {seconds:.0f} s; its last lines: {last}")


def keep(name: str) -> None:
    (common.RUN / "logs" / f"{name}.log").write_text(clog(name))


def kill(name: str) -> None:
    """Kill the container at once, as a crash would, and keep its log."""
    sh(["docker", "kill", name], check=False)
    keep(name)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    common.CONTAINERS.remove(name)


# --- the chain, read -----------------------------------------------------------------

def txs() -> list[dict]:
    """Every transaction on the chain, in order, with its block and index."""
    blocks = rpc_batch([("eth_getBlockByNumber", [hex(b), True]) for b in range(0, block_number() + 1)])
    return [{**t, "block": int(b["result"]["number"], 16), "index": int(t["transactionIndex"], 16)}
            for b in blocks for t in b["result"]["transactions"]]


def by(who: str, all_txs: list[dict]) -> list[dict]:
    return [t for t in all_txs if t["from"].lower() == ADDR[who].lower()]


def word(data: str, i: int) -> int:
    return int(data[10 + 64 * i:10 + 64 * (i + 1)], 16)


def calls_of(t: dict) -> str:
    """The function a transaction calls, by its signature."""
    for signature in SIGNATURES:
        if t["input"][:10] == sel(signature):
            return signature
    return t["input"][:10]


SIGNATURES = ["register()", "setOperator(address,address)", "acceptOperator(address)", "transfer(address,uint256)",
              "approve(address,uint256)", "bond(address,uint256)", "claim(address,uint256)",
              "withdrawParts(address,uint256[])", "fund(uint256,address,uint256)", "mint(address,uint256)",
              "redeemWinningTicket((address,address,uint256,uint256,uint256,bytes32,bytes),bytes,uint256)"]
CLAIMED = "Claimed(address,uint256,address,uint256,uint256,uint256,uint256)"


def part(n: int) -> tuple[int, int]:
    return tuple(words(C["Claims"], "partOf(address,address,uint256)", ADDR["H"], ADDR["N"], n))


def claimed_rounds() -> list[int]:
    return [int(x["topics"][2], 16) for x in logs(C["Claims"], CLAIMED) if int(x["topics"][1], 16) == int(ADDR["N"], 16)]


def steps_from_chain(all_txs: list[dict]) -> list[dict]:
    """The case's steps that N, H and P sent, as the chain shows them. The
    faucet's grant to N and N's move of it to H are one grant_lpt to H: a
    ledger case has no transfer of LPT between holders, and the grant came
    from the genesis supply."""
    out = []
    for t in all_txs:
        who = NAMES.get(t["from"].lower())
        f = calls_of(t)
        fields = None
        if who == "N" and f == "register()":
            fields = {"action": "register", "node": "N"}
        elif who == "N" and f == "setOperator(address,address)":
            fields = {"action": "set_operator", "node": "N", "next": "H"}
        elif who == "N" and f == "transfer(address,uint256)":
            fields = {"action": "grant_lpt", "to": "H", "amount_wei": word(t["input"], 1)}
        elif who == "H" and f == "acceptOperator(address)":
            fields = {"action": "accept_operator", "caller": "H", "node": "N"}
        elif who == "H" and f == "bond(address,uint256)":
            fields = {"action": "bond", "holder": "H", "node": "N", "amount_wei": word(t["input"], 1)}
        elif who == "H" and f == "claim(address,uint256)":
            got = [x for x in receipt(t["hash"])["logs"] if x["topics"][0] == topic(CLAIMED)]
            fields = {"action": "claim", "caller": "H", "node": "N", "earned_round": word(t["input"], 1),
                      "score": int(got[0]["data"][2 + 64:2 + 128], 16)}
        elif who == "H" and f == "withdrawParts(address,uint256[])":
            count = word(t["input"], 2)
            fields = {"action": "withdraw_parts", "holder": "H", "node": "N",
                      "rounds": [word(t["input"], 3 + i) for i in range(count)]}
        elif who == "P" and f == "fund(uint256,address,uint256)":
            deposit, earmark = word(t["input"], 0), word(t["input"], 2)
            fields = {"action": "fund", "payer": "P", "deposit_micro_usdc": deposit}
            if earmark:
                fields["node"] = name("0x" + t["input"][10 + 64 + 24:10 + 128])
            fields["earmark_micro_usdc"] = earmark
        if fields:
            out.append({"fields": fields, "block": t["block"], "index": t["index"], "seq": len(out), "tx": t["hash"]})
    return out


def unpaid(paid: list[int], joined: int, down: tuple[int, int], stopped: int) -> tuple[list[int], list[int], list[int]]:
    """The rounds the node was up for whole, from the one after its join to
    the one before it was stopped, less those from its kill to its being up
    again; of those, the ones it was not paid in; and the rounds it was paid
    in more than once."""
    whole = [r for r in range(joined + 1, stopped) if not down[0] <= r <= down[1]]
    return whole, [r for r in whole if r not in paid], sorted({r for r in paid if paid.count(r) > 1})


def explorer_status() -> dict:
    return json.loads(explorer_get("/status.json"))


def explorer_get(path: str) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT['explorer']}{path}", timeout=60) as r:
        return r.read().decode()


def unreachable_start(label: str, rpc: str) -> tuple[int, str]:
    """A container on a data folder of its own whose RPC provider fails from
    the first request: its exit code and its whole Docker output."""
    data = common.RUN / f"container-{label}"
    data.mkdir()
    name = f"lp2-demo-node-{BASE}-{label}"
    argv = container_argv(name, None, (), rpc=rpc, data=data)
    argv.insert(2, "--rm")
    common.CONTAINERS.append(name)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    common.CONTAINERS.remove(name)
    said = done.stdout + done.stderr
    (common.RUN / "logs" / f"{name}.log").write_text(said)
    failed = next((x for x in said.splitlines() if "lp2-stake state:" in x), "")
    say(f"  {label}: exit {done.returncode}: {failed[:240]}")
    return done.returncode, said


def main() -> int:
    open_log()
    started = time.time()
    say("The operator container: one command runs a node that joins the testnet, is paid and claims, with nobody acting")
    say("\n1. Setting up stage 4's system and the standing services")
    preflight(["chain", "orch", "signer_p", "signer_p_metrics", "explorer", "sender"])
    host = gateway()
    node_bin = fetch_node()
    common.PROGRAMS.update({"lp2-sender": "./cmd/lp2-sender", "lp2-faucet": "./cmd/lp2-faucet"})
    build_ours()
    build_image()
    start_chain(host)
    pw = common.RUN / "pw"
    pw.write_text("pw-" + os.urandom(8).hex())
    pw.chmod(0o600)
    for who in ("deployer", "P", "Keeper", "F"):
        new_key(who)
        if who != "deployer":
            light.KEYS[who] = s3.KEYS[who] = keystore(who, pw)
    ADDR["Treasury"] = "0x" + os.urandom(20).hex()
    NAMES[ADDR["Treasury"].lower()] = "Treasury"
    first_block = block_number()
    bb.deploy_stage4()
    send(KEY["deployer"], C["TestLPT"], "transfer(address,uint256)", ADDR["F"], light.FAUCET_LPT)
    s3.start_keeper("--parameters-address", C["Parameters"])
    start_signer("P", node_bin, light.KEYS["P"], pw, PORT["signer_p"], PORT["signer_p_metrics"], [PORT["orch"]])
    light.start_sender(host, "--mint", "0")
    write_deployment(first_block)
    DATA.mkdir()
    say(f"  stage 4's contracts on {common.RPC} (and on {host} for the container), a block a second, "
        f"{MANIFEST['chain']['round_length_blocks']} blocks a round; the keeper starts each round")
    say(f"  lp2-sender answers joins on {host}:{light.SENDER}; P's signer on loopback; the faucet is not started yet")
    say(f"  the deployment file the container reads: {where(DEPLOYMENT)}; its data folder: {where(DATA)}")

    say("\n3. The container makes its keys and asks for test ETH")
    one = run_node()
    line = wait_log(one, "node key N", 120)
    m = re.search(r"node key N (0x[0-9a-fA-F]{40}); operator key H (0x[0-9a-fA-F]{40})", line)
    for who, addr in zip(("N", "H"), m.groups()):
        ADDR[who] = addr
        NAMES[addr.lower()] = who
    say(f"  {line.split(' ', 1)[1]}")
    say(f"  {wait_log(one, 'fund both keys', 60).split(' ', 1)[1]}")
    keys = {f: (DATA / "keys" / f).read_bytes() for f in ("node.json", "operator.json", "password")}
    printed = clog(one)
    secret = json.loads(keys["node.json"])["crypto"]["ciphertext"]
    check("the container printed neither the password nor a keystore", (keys["password"].decode() in printed, secret in printed),
          (False, False))
    for who in ("N", "H"):
        sh(["cast", "send", "--rpc-url", common.RPC, "--private-key", KEY["deployer"], ADDR[who], "--value", str(FUNDED_WEI)])
    say(f"  the demo sends N and H {FUNDED_WEI // 10**18} ETH each; from here it only watches, kills and restarts")

    say("\n4. Killed while it waits for the faucet's grant")
    wait_log(one, "waiting for the faucet's grant", 120)
    kill(one)
    registered = lambda: [x for x in logs(C["Registry"], "NodeRegistered(address,uint256)")  # noqa: E731
                          if int(x["topics"][1], 16) == int(ADDR["N"], 16)]
    check("killed waiting for the grant: N registered once, holds no LPT", (len(registered()), light.lpt_of("N")), (1, 0))
    two = run_node(hold="approved")
    wait_log(two, "waiting for the faucet's grant", 120)
    light.start_faucet()
    say("  restarted; it waits for the grant again, and the faucet is started")
    wait_log(two, "holding after approved", 180)
    say("\n5. Killed between H's approval and H's bond")
    kill(two)
    allowance = call(C["TestLPT"], "allowance(address,address)", ADDR["H"], C["StakeLedger"])
    check("killed after the approval: H's allowance is the grant, H holds it, nothing bonded, H is N's operator",
          (allowance, light.lpt_of("H"), call(C["StakeLedger"], "stayingOf(address,address)", ADDR["H"], ADDR["N"]),
           f"0x{call(C['Registry'], 'operatorOf(address)', ADDR['N']):040x}"),
          (light.GRANT, light.GRANT, 0, ADDR["H"].lower()))

    say("\n6. Restarted; a second container on the same data folder refuses to start")
    three = run_node(hold="claimed")
    wait_log(three, "funded:", 60)
    other = f"lp2-demo-node-{BASE}-second"
    argv = container_argv(other, None, ports=())
    argv.insert(2, "--rm")
    try:
        second = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        code, said = second.returncode, (second.stdout + second.stderr).strip().splitlines()
    except subprocess.TimeoutExpired as e:
        subprocess.run(["docker", "rm", "-f", other], capture_output=True)
        code, said = "still running after 60 s", (e.stdout or b"").decode(errors="replace").strip().splitlines()
    say(f"  the second container: exit {code}, {said[-1] if said else ''}")
    check("a second container on the same data folder exits 1, refusing", (code,
          any("another container is running" in s for s in said)), (1, True))
    check("the first container runs on, and the keys are as they were",
          (running(three), {f: (DATA / "keys" / f).read_bytes() for f in keys} == keys), (True, True))

    say("\n7. It bonds, starts the node, joins and is paid; killed between its first claim and the withdrawal")
    wait_log(three, "counts for N in round", 4 * 60)
    wait_log(three, "starting the node", 60)
    STARTED["block"] = block_number()
    say(f"  {wait_log(three, 'joined:', 120).split(' ', 1)[1][:200]}")
    ROUNDS["joined"] = current_round()
    wait_log(three, "holding after claimed", RUN_LIMIT)
    ROUNDS["killed"] = current_round()
    kill(three)
    rounds = claimed_rounds()
    n = rounds[0]
    check(f"killed after claiming round {n}: one claim, H's part not yet withdrawn",
          (len(rounds), part(n)[1]), (1, 0))

    say("\n8. Restarted, it withdraws the part and claims nothing again")
    four = run_node()
    wait_log(four, "the node is up", 120)
    ROUNDS["up"] = current_round()
    say(f"  {wait_log(four, 'sent: withdraw-parts', 120).split('parts: ', 1)[-1][:200]}")
    got_part, withdrawn = part(n)
    check(f"round {n}: H's part withdrawn, and above zero", (withdrawn, got_part > 0), (1, True))
    status = explorer_status()
    page = urllib.request.urlopen(f"http://127.0.0.1:{PORT['explorer']}/", timeout=30).read().decode()
    ports = sh(["docker", "port", four])
    check("the explorer is published on the host's loopback only", f"8080/tcp -> 127.0.0.1:{PORT['explorer']}" in ports, True)
    ROUNDS["stopped"] = current_round()
    sh(["docker", "stop", "-t", "20", four])
    keep(four)
    subprocess.run(["docker", "rm", "-f", four], capture_output=True)
    common.CONTAINERS.remove(four)
    for p in ("sender", "faucet", "keeper", "signer-P"):
        stop(p)

    say("\n9. RPC providers that fail, with a key in their URL; and public port 09000, where the responder listened before")
    provider = Provider(host)
    code, said = unreachable_start(
        "unreachable", f"https://synthetic-user:{SYNTHETIC_PASSWORD}@127.0.0.1:1/v3/{SYNTHETIC_KEY}?apikey={SYNTHETIC_KEY}")
    check("an unreachable provider: the log names the step, the provider's host and the failure, and no credential in its URL",
          (code, "gave up after 10 tries: read_state" in said,
           "lp2-stake state: the RPC provider https://127.0.0.1:1 could not be reached" in said,
           [c for c in CREDENTIALS if c in said]), (1, True, True, []))
    provider.reject()
    code, said = unreachable_start("rejected", KEYED)
    check("a provider that rejects the request and echoes the key and the escaped password: the log names the step, "
          "the provider's host and the HTTP status, and no credential",
          (code, "gave up after 10 tries: read_state" in said,
           f"lp2-stake state: the RPC provider http://host.docker.internal:{PROVIDER} refused the request (HTTP 401)" in said,
           [c for c in CREDENTIALS if c in said]), (1, True, True, []))
    provider.rejecting = False
    five = run_node(hold="started", ports=("explorer",), public="http://127.0.0.1:09000", rpc=KEYED)
    ports = wait_log(five, "ports:", 60).split(" ", 1)[1]
    say(f"  {ports}")
    check("public port 09000 is port 9000, and the responder moves off it",
          ("the node on 0.0.0.0:9000," in ports, "the responder on 127.0.0.1:9001," in ports), (True, True))
    wait_log(five, "holding after started", 180)
    check("on public port 09000, the node comes up and its discovery lists lp2-responder", running(five), True)
    shown = {"the explorer's JSON, the provider answering": explorer_get("/status.json")}
    provider.reject()
    time.sleep(15)
    shown["the explorer's JSON, the provider rejecting the key"] = explorer_get("/status.json")
    shown["the explorer's page, the provider rejecting the key"] = explorer_get("/")
    provider.refuse()
    time.sleep(15)
    shown["the explorer's JSON, the provider refusing connections"] = explorer_get("/status.json")
    shown["the explorer's page, the provider refusing connections"] = explorer_get("/")
    kill(five)
    failing = [json.loads(shown[k])["unavailable"] for k in shown if "JSON" in k and "answering" not in k]
    say(f"  the explorer, its provider failing: {failing[0][0][:160]}; {failing[1][0][:160]}")
    told = f"the chain's newest block: the RPC provider http://host.docker.internal:{PROVIDER} "
    check("the explorer shows each failure as unavailable, naming what it could not read and the failure's category",
          [any(x.startswith(told + why) for x in f) for f, why in zip(failing, ("refused the request (HTTP 401)",
                                                                               "could not be reached"))], [True, True])
    for label in (five, f"lp2-demo-node-{BASE}-unreachable", f"lp2-demo-node-{BASE}-rejected"):
        shown[f"the Docker output of {label}"] = (common.RUN / "logs" / f"{label}.log").read_text()
    for folder in (DATA, common.RUN / "container-unreachable", common.RUN / "container-rejected"):
        for f in sorted((folder / "logs").iterdir()):
            shown[where(f)] = f.read_text(errors="replace")
    say(f"  read for the key and the password, as written and decoded: {len(shown)} outputs, "
        f"{sum(len(v) for v in shown.values())} characters")
    check("no credential in the RPC URL appears in any Docker output, file under logs, or explorer page or JSON",
          [(k, c) for k, v in shown.items() for c in CREDENTIALS if c in v], [])
    s3.wait("N's last transactions to be mined", lambda: rpc("eth_getTransactionCount", [ADDR["N"], "pending"]) ==
            rpc("eth_getTransactionCount", [ADDR["N"], "latest"]), 60)
    time.sleep(3)

    say("\n10. The chain: each step once, and N's key used by the node alone once it started")
    all_txs = txs()
    for who in ("N", "H"):
        statuses = [x["result"]["status"] for x in rpc_batch([("eth_getTransactionReceipt", [t["hash"]])
                                                                for t in by(who, all_txs)])]
        check(f"every transaction of {who} succeeded", set(statuses), {"0x1"})
    kinds = lambda who, upto=None: [calls_of(t) for t in by(who, all_txs) if upto is None or t["block"] <= upto]  # noqa: E731
    before = kinds("N", STARTED["block"])
    after = {(calls_of(t), t["to"].lower()) for t in by("N", all_txs) if t["block"] > STARTED["block"]}
    redeem = "redeemWinningTicket((address,address,uint256,uint256,uint256,bytes32,bytes),bytes,uint256)"
    check("N's key before the node started: one registration, one proposal of H, one move of its grant",
          before, ["register()", "setOperator(address,address)", "transfer(address,uint256)"])
    check("N's key after the node started: only the node's own redemptions, at the Broker",
          after, {(redeem, C["Broker"].lower())})
    h = kinds("H")
    check("H: one acceptance, one approval, one bond", [h.count(k) for k in ("acceptOperator(address)", "approve(address,uint256)",
                                                                            "bond(address,uint256)")], [1, 1, 1])
    claims = claimed_rounds()
    check("no round claimed twice", len(claims), len(set(claims)))
    check("the faucet granted N once", [r for r, _ in light.granted_by_faucet()].count("N"), 1)
    check("N registered once", len(registered()), 1)
    relays = sorted(x["round"] for x in light.relays().get("N", []))
    whole, missed, twice = unpaid(relays, ROUNDS["joined"], (ROUNDS["killed"], ROUNDS["up"]), ROUNDS["stopped"])
    say(f"  the sender's relays to N, by round: {relays}; joined in round {ROUNDS['joined']}, killed in "
        f"{ROUNDS['killed']}, up again in {ROUNDS['up']}, stopped in {ROUNDS['stopped']}")
    check(f"N was paid once in each round it was up for whole ({len(whole)} rounds: {whole})",
          (len(whole) >= 3, missed, twice), (True, [], []))

    say(f"\n11. The run as a ledger case, and the explorer's figures for round {n}")
    extra = steps_from_chain(all_txs) + s3.keeper_steps()
    start_block = int(next(x for x in logs(C["RoundClock"], "RoundStarted(uint256,uint256,bytes32)")
                           if int(x["topics"][1], 16) == n)["blockNumber"], 16)
    extra.append({"fields": {"action": "observe_stake", "node": "N"}, "block": start_block, "index": None,
                  "seq": 3 * 10**9, "shown": (call(C["StakeLedger"], "stakeAt(address,uint256)", ADDR["N"], n),
                                              call(C["StakeLedger"], "totalStakeAt(uint256)", n))})
    ordered, views, calc = reconcile("The container's run", extra=extra)
    scan(ordered, ["Registry", "StakeLedger", "Claims", "Broker"])
    write_case("container-run", ordered, views)

    row = next(r for r in status["node"]["rounds"] if r["round"] == n)
    observed = next(o for o in calc["observed"] if "stake_wei" in o)
    shares = calc["shares_wei"].get(str(n), {"validators_wei": "0", "treasury_wei": "0"})
    paid = int(calc["minted_for_round_wei"][str(n)]) - int(shares["validators_wei"]) - int(shares["treasury_wei"])
    claim_step = next(s for s in ledgercase.steps(ordered) if s["action"] == "claim" and s["earned_round"] == str(n))
    delay, window = int(PARAMS["reward_delay_rounds"]), int(PARAMS["claim_window_rounds"])
    check("explorer: no figure unavailable", status["unavailable"], [])
    for label, page_v, want, fmt in (
            (f"round {n}: fees counted for N", int(row["fees_usdc_micro"]), int(calc["fees_micro_usdc"][str(n)]["N"]), money),
            (f"round {n}: N's stake", int(row["stake_wei"]), int(observed["stake_wei"]), lpt),
            (f"round {n}: the score its claim applied", int(row["claim_score"]), int(claim_step["score"]), str),
            (f"round {n}: claim paid", int(row["paid_wei"]), paid, lpt),
            (f"round {n}: pot (N's cut is 0)", int(row["pot_wei"]), paid, lpt),
            (f"round {n}: N's own part (N holds no bond)", int(row["part_wei"]), 0, lpt)):
        s3.side(f"Explorer: {label}", page_v, want, fmt)
    check(f"explorer: round {n} claimed, claimable from round {n + delay} to {n + delay + window}",
          (row["state"], row["claimable_from"], row["claimable_until"]), ("claimed", n + delay, n + delay + window))
    check("explorer: N registered, H its operator key", (status["node"]["registered"], status["node"]["operator"].lower()),
          (True, ADDR["H"].lower()))
    withdrawn_parts = sum(part(r)[0] for r in claims if part(r)[1])
    s3.side("H's parts withdrawn (the explorer shows N's, not H's)", withdrawn_parts, int(calc["lpt_wei"]["H"]), lpt)
    check("the explorer's page reloads itself and carries no script", ('http-equiv="refresh"' in page, "<script" in page), (True, False))
    return finish(started, f"the container registered, was granted, bonded, joined, was paid once a round and claimed and "
                           f"withdrew round {n}, through three kills, each step once; a second container refused")
