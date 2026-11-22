// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {Controller} from "../src/authority/Controller.sol";
import {Registry} from "../src/authority/Registry.sol";
import {RoundClock} from "../src/clock/RoundClock.sol";
import {Broker} from "../src/money/Broker.sol";
import {MockUSDC} from "../src/money/MockUSDC.sol";
import {Parameters} from "../src/authority/Parameters.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// A fresh stage 2 deployment, the shared examples, and the steps a test
/// takes as a participant: register, fund, sign and redeem a ticket.
abstract contract Stage2 {
    RoundClock internal clock;
    Registry internal registry;
    MockUSDC internal usdc;
    Broker internal broker;
    Controller internal controller;
    Parameters internal params;

    string internal vectors;

    /// Deploys the five contracts at block 100 (round 1 starts there), then
    /// Parameters with the authority G and no token, pool or score source.
    /// The Registry is given the address the Broker will be created at. No
    /// Buyback: nobody may draw the network share.
    function deploy() internal {
        deploy(address(0));
    }

    /// As deploy(), with the Broker given the Buyback that may draw.
    function deploy(address buyback) internal {
        deployBase(buyback, vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 5));
        newParameters(address(0), address(0), bytes32(0), address(0));
    }

    /// The five contracts, the Broker given `parameters`, the address the
    /// caller then creates Parameters at with newParameters.
    function deployBase(address buyback, address parameters) internal {
        vm.roll(100);
        clock = new RoundClock();
        usdc = new MockUSDC();
        address next = vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        registry = new Registry(clock, next);
        broker = new Broker(usdc, registry, clock, Parameters(parameters), buyback);
        require(address(broker) == next, "Broker at the address the Registry was given");
        controller = new Controller(address(registry), address(clock), address(broker));
        params = Parameters(parameters);
    }

    /// Parameters with the authority G (the shared examples' governance
    /// address) and the deployment's score source and pool, at the address
    /// deployBase was given.
    function newParameters(address scoreSource, address pool, bytes32 poolHash, address lpt_) internal {
        address at = address(params);
        params = new Parameters(who("G"), clock, scoreSource, pool, poolHash, address(usdc), lpt_);
        require(address(params) == at, "Parameters at the address the Broker was given");
    }

    function loadVectors() internal {
        loadVectors("october-main-text-v1");
    }

    function loadVectors(string memory profile) internal {
        vectors = vm.readFile(string.concat(vm.projectRoot(), "/../vectors/", profile, ".json"));
    }

    function param(string memory name) internal view returns (uint256) {
        return vm.parseUint(vm.parseJsonString(vectors, string.concat(".parameters.", name)));
    }

    /// Index of a case in the examples file.
    function caseIndex(string memory id) internal view returns (uint256) {
        string[] memory ids = abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
        for (uint256 i = 0; i < ids.length; i++) {
            if (eq(ids[i], id)) return i;
        }
        revert(string.concat("no case ", id));
    }

    // --- participants ---------------------------------------------------

    /// A throwaway key per name, made inside the test.
    function key(string memory name) internal pure returns (uint256) {
        return uint256(keccak256(abi.encodePacked("lp2 test key ", name)));
    }

    function who(string memory name) internal pure returns (address) {
        return vm.addr(key(name));
    }

    /// Starts rounds, 50 blocks apart, until `round` is current.
    function toRound(uint256 round) internal {
        while (clock.currentRound() < round) {
            vm.roll(block.number + clock.roundLength());
            clock.initializeRound();
        }
    }

    function register(string memory node) internal {
        vm.prank(who(node));
        registry.register();
    }

    /// Mints the payer exactly what it funds, then funds. Returns the
    /// revert data, empty when the step took effect. A refused step leaves
    /// the payer holding nothing it did not have.
    function fund(string memory payer, uint256 dep, address node, uint256 mark) internal returns (bytes memory) {
        address p = who(payer);
        usdc.mint(p, dep + mark);
        vm.prank(p);
        usdc.approve(address(broker), dep + mark);
        vm.prank(p);
        (bool ok, bytes memory ret) = address(broker).call(abi.encodeCall(Broker.fund, (dep, node, mark)));
        if (!ok) {
            vm.prank(p);
            usdc.transfer(address(0xdead), dep + mark);
            return ret;
        }
        return "";
    }

    /// `node` sets its own payout, as its operator key until it rotates.
    /// Returns the revert data, empty on success.
    function setPayout(string memory node, address payout) internal returns (bytes memory) {
        return setPayoutAs(who(node), who(node), payout);
    }

    /// `caller` sets `node`'s payout (R-01, R-26). Returns the revert data,
    /// empty on success.
    function setPayoutAs(address caller, address node, address payout) internal returns (bytes memory) {
        vm.prank(caller);
        (bool ok, bytes memory ret) = address(registry).call(abi.encodeCall(Registry.setPayout, (node, payout)));
        return ok ? bytes("") : ret;
    }

    /// `caller` proposes `next` as `node`'s operator key (R-26). Returns the
    /// revert data, empty on success.
    function setOperatorAs(address caller, address node, address next) internal returns (bytes memory) {
        vm.prank(caller);
        (bool ok, bytes memory ret) = address(registry).call(abi.encodeCall(Registry.setOperator, (node, next)));
        return ok ? bytes("") : ret;
    }

    /// `caller` accepts the proposal for `node` (R-26). Returns the revert
    /// data, empty on success.
    function acceptOperatorAs(address caller, address node) internal returns (bytes memory) {
        vm.prank(caller);
        (bool ok, bytes memory ret) = address(registry).call(abi.encodeCall(Registry.acceptOperator, (node)));
        return ok ? bytes("") : ret;
    }

    /// A name in the examples; `zero` is the address zero.
    function named(string memory name) internal pure returns (address) {
        return keccak256(bytes(name)) == keccak256("zero") ? address(0) : who(name);
    }

    /// `payer` asks to release `amount` from its deposit (node address(0))
    /// or its earmark for `node`. Returns the revert data, empty on success.
    function requestRelease(string memory payer, address node, uint256 amount) internal returns (bytes memory) {
        vm.prank(who(payer));
        (bool ok, bytes memory ret) = address(broker).call(abi.encodeCall(Broker.requestRelease, (node, amount)));
        return ok ? bytes("") : ret;
    }

    /// `payer` releases what is pending on its deposit (node address(0)) or
    /// its earmark for `node`. Returns the revert data, empty on success.
    function release(string memory payer, address node) internal returns (bytes memory) {
        vm.prank(who(payer));
        (bool ok, bytes memory ret) = address(broker).call(abi.encodeCall(Broker.release, (node)));
        return ok ? bytes("") : ret;
    }

    /// The ticket, signature and random value a payer gives a node, as the
    /// node's signer builds them. Every ticket wins.
    function ticket(string memory id, string memory payer, address node, uint256 created, uint256 face)
        internal
        view
        returns (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand)
    {
        rand = uint256(keccak256(abi.encodePacked("rand ", id)));
        t = ITicketBroker.Ticket({
            recipient: node,
            sender: who(payer),
            faceValue: face,
            winProb: type(uint256).max,
            senderNonce: uint256(keccak256(abi.encodePacked("nonce ", id))) % 2 ** 32,
            recipientRandHash: keccak256(abi.encodePacked(rand)),
            auxData: abi.encodePacked(created, clock.blockHashForRound(created))
        });
        sig = signTicket(key(payer), t);
    }

    function ticketHash(ITicketBroker.Ticket memory t) internal pure returns (bytes32) {
        return keccak256(
            abi.encodePacked(
                t.recipient, t.sender, t.faceValue, t.winProb, t.senderNonce, t.recipientRandHash, t.auxData
            )
        );
    }

    function signTicket(uint256 pk, ITicketBroker.Ticket memory t) internal pure returns (bytes memory) {
        bytes32 digest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", ticketHash(t)));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    /// Submits as the node does. Returns the revert data, empty on success.
    function redeem(ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) internal returns (bytes memory) {
        vm.prank(t.recipient);
        (bool ok, bytes memory ret) = address(broker).call(abi.encodeCall(Broker.redeemWinningTicket, (t, sig, rand)));
        return ok ? bytes("") : ret;
    }

    // --- refusals -------------------------------------------------------

    /// The stable code a call was refused with: a custom error's name, or
    /// the kept revert text.
    function code(bytes memory ret) internal pure returns (string memory) {
        string[28] memory codes = [
            "NOT_REGISTERED",
            "TICKET_EXPIRED",
            "TICKET_USED",
            "TICKET_INVALID",
            "NOTHING_TO_PAY",
            "AMOUNT_TOO_LARGE",
            "RELEASE_NOT_READY",
            "PAYOUT_INVALID",
            "ROUND_NOT_OVER",
            "NOT_AUTHORIZED",
            "UNBOND_NOT_READY",
            "CUT_OUT_OF_RANGE",
            "CLAIM_TOO_EARLY",
            "CLAIM_EXPIRED",
            "ALREADY_CLAIMED",
            "SCORE_OUT_OF_RANGE",
            "NOT_CLAIMED",
            "NO_SUCH_ROUND",
            "SAME_NODE",
            "NOT_OPTED_IN",
            "STAKE_TOO_LOW",
            "NOT_VALIDATOR",
            "NOT_ADJUSTABLE",
            "SCHEDULE_RAISED",
            "VALUE_OUT_OF_RANGE",
            "IDENTITY_NOT_WHOLE",
            "NOTHING_PENDING",
            "OPERATOR_INVALID"
        ];
        if (ret.length == 4) {
            for (uint256 i = 0; i < codes.length; i++) {
                if (bytes4(ret) == bytes4(keccak256(bytes(string.concat(codes[i], "()"))))) return codes[i];
            }
        }
        // R-16's POOL_INVALID names the pool; its code is the name alone.
        if (ret.length == 36 && bytes4(ret) == bytes4(keccak256("POOL_INVALID(address)"))) return "POOL_INVALID";
        if (ret.length > 4 && bytes4(ret) == bytes4(keccak256("Error(string)"))) {
            bytes memory body = new bytes(ret.length - 4);
            for (uint256 i = 0; i < body.length; i++) {
                body[i] = ret[i + 4];
            }
            return abi.decode(body, (string));
        }
        return "unknown revert";
    }

    function eq(string memory a, string memory b) internal pure returns (bool) {
        return keccak256(bytes(a)) == keccak256(bytes(b));
    }

    function expectEq(uint256 got, uint256 want, string memory what) internal pure {
        if (got != want) {
            revert(string.concat(what, ": got ", vm.toString(got), ", want ", vm.toString(want)));
        }
    }
}
