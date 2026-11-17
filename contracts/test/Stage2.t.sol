// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {Stage2} from "./Stage2.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// What the node relies on beyond the ledger cases: the names it looks up,
/// what it is shown of a payer, the round clock it signs against, and the
/// ticket checks it treats as final.
contract Stage2Test is Stage2 {
    function setUp() external {
        loadVectors();
        deploy();
    }

    function test_controller_answers_the_node_names() external view {
        require(controller.getContract(keccak256("BondingManager")) == address(registry), "BondingManager");
        require(controller.getContract(keccak256("RoundsManager")) == address(clock), "RoundsManager");
        require(controller.getContract(keccak256("TicketBroker")) == address(broker), "TicketBroker");
        require(controller.getContract(keccak256("Minter")) == address(0), "any other name");
    }

    /// R-01: free, permanent, and registering again changes nothing.
    function test_register_is_free_and_permanent() external {
        address n = who("N");
        require(!registry.isRegistered(n) && !registry.isActiveTranscoder(n), "not yet registered");
        register("N");
        register("N");
        require(registry.isRegistered(n) && registry.isActiveTranscoder(n), "registered");
        require(registry.payoutOf(n) == n, "paid at its own address");
    }

    /// R-21: after ledger-demo's funding, the node is shown the deposit and
    /// its own earmark, and the signer the earmarks summed. Another node
    /// is shown no earmark.
    function test_node_is_shown_the_deposit_and_its_earmark() external {
        string memory s = string.concat(".cases[", vm.toString(caseIndex("ledger-demo")), "].inputs.steps[1]");
        require(eq(vm.parseJsonString(vectors, string.concat(s, ".action")), "fund"), "ledger-demo step 2 funds");
        uint256 dep = vm.parseUint(vm.parseJsonString(vectors, string.concat(s, ".deposit_micro_usdc")));
        uint256 mark = vm.parseUint(vm.parseJsonString(vectors, string.concat(s, ".earmark_micro_usdc")));
        register("N");
        register("M");
        require(fund("P", dep, who("N"), mark).length == 0, "fund");

        (ITicketBroker.Sender memory sender, ITicketBroker.ReserveInfo memory reserve) =
            broker.getSenderInfo(who("P"));
        expectEq(sender.deposit, dep, "deposit shown");
        expectEq(sender.withdrawRound, 0, "withdrawRound");
        expectEq(reserve.fundsRemaining, mark, "earmarks summed");
        expectEq(reserve.claimedInCurrentRound, 0, "claimedInCurrentRound");
        expectEq(broker.claimableReserve(who("P"), who("N")), mark, "N's earmark");
        expectEq(broker.claimableReserve(who("P"), who("M")), 0, "M's earmark");
    }

    /// R-18: a round lasts at least RoundLength blocks; starting the next
    /// one earlier is refused with ROUND_NOT_OVER. The round the node signs
    /// against is the round redemption checks.
    function test_round_starts_after_round_length() external {
        uint256 len = param("round_length_blocks");
        expectEq(clock.roundLength(), len, "roundLength");
        expectEq(clock.currentRound(), 1, "round 1 at deployment");
        require(clock.blockHashForRound(1) == blockhash(99), "round 1 hash");

        vm.roll(100 + len - 1);
        (bool ok, bytes memory ret) = address(clock).call(abi.encodeWithSignature("initializeRound()"));
        require(!ok && eq(code(ret), "ROUND_NOT_OVER"), "early start refused");
        expectEq(clock.currentRound(), 1, "still round 1");

        vm.roll(100 + len);
        clock.initializeRound();
        expectEq(clock.currentRound(), 2, "round 2");
        expectEq(clock.lastInitializedRound(), 2, "lastInitializedRound");
        require(clock.blockHashForRound(2) == blockhash(100 + len - 1), "round 2 hash");
        require(clock.blockHashForRound(2) != bytes32(0), "round 2 hash is set");
        require(clock.blockHashForRound(3) == bytes32(0), "round 3 not started");
    }

    /// R-18's example: round 2 starts one round length after deployment;
    /// nobody starts round 3 for five round lengths (250 blocks), and then
    /// round 3 starts with no round number skipped. A ticket signed in
    /// that long round 2 is paid in round 3.
    function test_ticket_signed_in_a_long_round_is_paid() external {
        uint256 len = param("round_length_blocks");
        vm.roll(100 + len);
        clock.initializeRound();
        expectEq(clock.currentRound(), 2, "round 2");
        register("N");
        require(fund("P", 1_000_000, address(0), 0).length == 0, "fund");

        vm.roll(100 + len + 5 * len - 1);
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket("long", "P", who("N"), 2, 10_001);

        vm.roll(100 + len + 5 * len);
        clock.initializeRound();
        expectEq(clock.currentRound(), 3, "round 3, none skipped");
        require(redeem(t, sig, rand).length == 0, "paid");
        expectEq(broker.feesOf(3, who("N")), t.faceValue, "fee counted in round 3");
    }

    // --- every cause of TICKET_INVALID, each moving nothing ----------------

    /// A ticket signed by anyone but its sender.
    function test_wrong_signature_is_refused() external {
        (ITicketBroker.Ticket memory t,, uint256 rand) = funded();
        bytes memory forged = signTicket(key("Q"), t);
        refusedAndNothingMoved(t, forged, rand, "TICKET_INVALID");
    }

    /// A random value that does not match the ticket's commitment.
    function test_wrong_random_value_is_refused() external {
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = funded();
        refusedAndNothingMoved(t, sig, rand + 1, "TICKET_INVALID");
    }

    /// auxData of 96 bytes, holding a valid round and hash first.
    function test_wrong_aux_data_length_is_refused() external {
        (ITicketBroker.Ticket memory t,, uint256 rand) = funded();
        t.auxData = abi.encodePacked(t.auxData, bytes32(0));
        refusedAndNothingMoved(t, signTicket(key("P"), t), rand, "TICKET_INVALID");
    }

    /// A creation round that has started, with another block hash.
    function test_wrong_block_hash_is_refused() external {
        (ITicketBroker.Ticket memory t,, uint256 rand) = funded();
        t.auxData = abi.encodePacked(clock.currentRound(), bytes32(uint256(1)));
        refusedAndNothingMoved(t, signTicket(key("P"), t), rand, "TICKET_INVALID");
    }

    /// A zero recipient, on a ticket its sender signed.
    function test_zero_recipient_is_refused() external {
        (ITicketBroker.Ticket memory t,, uint256 rand) = funded();
        t.recipient = address(0);
        refusedAndNothingMoved(t, signTicket(key("P"), t), rand, "TICKET_INVALID");
    }

    /// A zero sender, with a signature from which ecrecover returns the
    /// zero address.
    function test_zero_sender_is_refused() external {
        (ITicketBroker.Ticket memory t,, uint256 rand) = funded();
        t.sender = address(0);
        bytes memory sig = abi.encodePacked(bytes32(0), bytes32(0), uint8(27));
        refusedAndNothingMoved(t, sig, rand, "TICKET_INVALID");
    }

    /// A ticket that did not win: win probability 0.
    function test_losing_ticket_is_refused() external {
        (ITicketBroker.Ticket memory t,, uint256 rand) = funded();
        t.winProb = 0;
        refusedAndNothingMoved(t, signTicket(key("P"), t), rand, "TICKET_INVALID");
    }

    /// A ticket naming a round that has not started is refused with the
    /// kept text the node treats as final, and nothing moves.
    function test_round_not_started_is_refused_with_the_kept_text() external {
        (ITicketBroker.Ticket memory t,, uint256 rand) = funded();
        t.auxData = abi.encodePacked(clock.currentRound() + 1, bytes32(uint256(1)));
        bytes memory sig = signTicket(key("P"), t);
        refusedAndNothingMoved(t, sig, rand, "ticket creationRound does not have a block hash");
    }

    /// R-03 for the largest amounts: a ticket worth more than
    /// type(uint256).max / NODE_CUT is still paid, split by the same floor.
    function test_largest_payment_is_split() external {
        toRound(2);
        register("N");
        uint256 face = type(uint256).max / 2;
        require(fund("P", face, who("N"), 0).length == 0, "fund");
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket("large", "P", who("N"), 2, face);
        require(redeem(t, sig, rand).length == 0, "redeemed");
        uint256 node = face / 2;
        expectEq(usdc.balanceOf(who("N")), node, "node");
        expectEq(broker.networkHeld(), face - node, "network");
        expectEq(broker.totalFees(2), face, "fees");
    }

    function funded() internal returns (ITicketBroker.Ticket memory, bytes memory, uint256) {
        toRound(2);
        register("N");
        require(fund("P", 1_000_000, who("N"), 1_000_000).length == 0, "fund");
        return ticket("t1", "P", who("N"), 2, 10_001);
    }

    /// The ticket is refused with `want`, and no money, fee record or used
    /// ticket changed.
    function refusedAndNothingMoved(
        ITicketBroker.Ticket memory t,
        bytes memory sig,
        uint256 rand,
        string memory want
    ) internal {
        uint256 held = usdc.balanceOf(address(broker));
        (uint256 dep,,) = broker.earmarkOf(who("P"), address(0));
        (uint256 mark,,) = broker.earmarkOf(who("P"), who("N"));
        bytes memory ret = redeem(t, sig, rand);
        require(ret.length > 0, "paid, not refused");
        require(eq(code(ret), want), string.concat("refused with ", code(ret)));
        expectEq(usdc.balanceOf(address(broker)), held, "Broker USDC");
        expectEq(usdc.balanceOf(who("N")), 0, "node paid");
        expectEq(broker.networkHeld(), 0, "network");
        (uint256 depAfter,,) = broker.earmarkOf(who("P"), address(0));
        (uint256 markAfter,,) = broker.earmarkOf(who("P"), who("N"));
        expectEq(depAfter, dep, "deposit");
        expectEq(markAfter, mark, "earmark");
        expectEq(broker.feesOf(clock.currentRound(), who("N")), 0, "fees of N");
        expectEq(broker.totalFees(clock.currentRound()), 0, "fees");
        require(!broker.usedTickets(ticketHash(t)), "ticket unused");
    }
}
