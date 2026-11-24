// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {PaymentsFixture} from "./PaymentsFixture.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// The payout and release rules of R-01 and R-22 that no shared case
/// exercises. Amounts here are inputs, not rule outputs: each expected
/// figure follows from them by the rule the test names, and the release
/// wait is read from the shared examples.
contract AccountTest is PaymentsFixture {
    uint256 private constant DEPOSIT = 1_000_000;
    uint256 private constant EARMARK = 400_000;
    uint256 private wait;

    function setUp() external {
        loadVectors();
        deploy();
        wait = param("release_wait_rounds");
        toRound(2);
    }

    // --- payout (R-01) ---------------------------------------------------

    /// Setting a payout from an address that is not registered is refused,
    /// and changes nothing.
    function test_set_payout_by_unregistered_is_refused() external {
        refused(setPayout("X", who("W")), "NOT_REGISTERED");
        require(!registry.isRegistered(who("X")), "X not registered");
        require(registry.payoutOf(who("X")) == who("X"), "payout unchanged");
    }

    /// A payout at the Broker's own address is refused, and changes nothing.
    function test_payout_to_the_broker_is_refused() external {
        register("N");
        refused(setPayout("N", address(broker)), "PAYOUT_INVALID");
        require(registry.payoutOf(who("N")) == who("N"), "payout unchanged");
    }

    /// address(0) makes the payout the node itself again.
    function test_payout_zero_is_the_node_itself() external {
        register("N");
        ok(setPayout("N", who("W")));
        require(registry.payoutOf(who("N")) == who("W"), "W");
        ok(setPayout("N", address(0)));
        require(registry.payoutOf(who("N")) == who("N"), "the node itself");
    }

    /// Registering again changes nothing, the payout included.
    function test_registering_again_keeps_the_payout() external {
        register("N");
        ok(setPayout("N", who("W")));
        register("N");
        require(registry.payoutOf(who("N")) == who("W"), "payout kept");
    }

    // --- funding (R-21) --------------------------------------------------

    /// A fund step refused for its earmark moves nothing, its deposit part
    /// included.
    function test_refused_fund_moves_nothing() external {
        refused(fund("P", DEPOSIT, who("X"), EARMARK), "NOT_REGISTERED");
        (uint256 d,,) = broker.earmarkOf(who("P"), address(0));
        (uint256 e,,) = broker.earmarkOf(who("P"), who("X"));
        expectEq(d, 0, "deposit");
        expectEq(e, 0, "earmark");
        expectEq(usdc.balanceOf(address(broker)), 0, "Broker USDC");
    }

    // --- release (R-22) --------------------------------------------------

    /// A request above the balance is refused and leaves nothing pending;
    /// a request of exactly the balance is accepted, for the deposit and
    /// for an earmark.
    function test_request_above_the_balance_is_refused_and_exactly_it_accepted() external {
        funded();
        refused(requestRelease("P", address(0), DEPOSIT + 1), "AMOUNT_TOO_LARGE");
        refused(requestRelease("P", who("N"), EARMARK + 1), "AMOUNT_TOO_LARGE");
        pendingIs(address(0), 0, 0);
        pendingIs(who("N"), 0, 0);
        shown(DEPOSIT, EARMARK);

        ok(requestRelease("P", address(0), DEPOSIT));
        ok(requestRelease("P", who("N"), EARMARK));
        pendingIs(address(0), DEPOSIT, 2 + wait);
        pendingIs(who("N"), EARMARK, 2 + wait);
        shown(0, 0);
    }

    /// A release with none pending is refused.
    function test_release_with_none_pending_is_refused() external {
        funded();
        refused(release("P", address(0)), "RELEASE_NOT_READY");
        refused(release("P", who("N")), "RELEASE_NOT_READY");
        expectEq(usdc.balanceOf(who("P")), 0, "released");
    }

    /// A new request replaces the pending one and restarts the wait.
    function test_new_request_restarts_the_wait() external {
        funded();
        uint256 first = DEPOSIT / 2;
        uint256 second = DEPOSIT / 4;
        ok(requestRelease("P", address(0), first));
        toRound(3);
        ok(requestRelease("P", address(0), second));
        pendingIs(address(0), second, 3 + wait);
        shown(DEPOSIT - second, EARMARK);

        toRound(2 + wait);
        refused(release("P", address(0)), "RELEASE_NOT_READY");
        toRound(3 + wait);
        ok(release("P", address(0)));
        expectEq(usdc.balanceOf(who("P")), second, "released the second request");
        balanceIs(address(0), DEPOSIT - second);
        holds();
    }

    /// A request of 0 cancels the pending one: the whole balance is shown
    /// again, and nothing can be released.
    function test_request_of_zero_cancels() external {
        funded();
        ok(requestRelease("P", who("N"), EARMARK / 2));
        shown(DEPOSIT, EARMARK - EARMARK / 2);
        ok(requestRelease("P", who("N"), 0));
        pendingIs(who("N"), 0, 0);
        shown(DEPOSIT, EARMARK);

        toRound(2 + wait);
        refused(release("P", who("N")), "RELEASE_NOT_READY");
        expectEq(usdc.balanceOf(who("P")), 0, "released");
        balanceIs(who("N"), EARMARK);
    }

    /// A release clears its request, so releasing twice pays once.
    function test_release_clears_its_request() external {
        funded();
        uint256 amount = EARMARK / 4;
        ok(requestRelease("P", who("N"), amount));
        toRound(2 + wait);
        ok(release("P", who("N")));
        pendingIs(who("N"), 0, 0);
        refused(release("P", who("N")), "RELEASE_NOT_READY");
        expectEq(usdc.balanceOf(who("P")), amount, "released once");
        balanceIs(who("N"), EARMARK - amount);
        shown(DEPOSIT, EARMARK - amount);
        holds();
    }

    /// A redemption lowers an earmark's pending amount to the balance left
    /// where it is above it, and keeps the request's round; release then
    /// pays only what is left.
    function test_redemption_lowers_a_pending_earmark_release_to_what_is_left() external {
        register("N");
        ok(fund("P", 0, who("N"), EARMARK));
        ok(requestRelease("P", who("N"), EARMARK - EARMARK / 5));
        redeemFace(EARMARK / 2);
        uint256 left = EARMARK - EARMARK / 2;
        pendingIs(who("N"), left, 2 + wait);
        shown(0, 0);

        toRound(2 + wait);
        ok(release("P", who("N")));
        expectEq(usdc.balanceOf(who("P")), left, "released what was left");
        balanceIs(who("N"), 0);
        holds();
    }

    /// A pending amount at or below the balance left is kept as it is.
    function test_redemption_keeps_a_pending_release_below_what_is_left() external {
        register("N");
        ok(fund("P", 0, who("N"), EARMARK));
        uint256 amount = EARMARK / 5;
        ok(requestRelease("P", who("N"), amount));
        redeemFace(EARMARK / 2);
        pendingIs(who("N"), amount, 2 + wait);
        shown(0, EARMARK - EARMARK / 2 - amount);
    }

    /// The signer is shown every earmark less its pending release, summed.
    function test_signer_is_shown_each_earmark_less_its_pending_release() external {
        register("N");
        register("M");
        ok(fund("P", 0, who("N"), EARMARK));
        ok(fund("P", 0, who("M"), EARMARK / 2));
        ok(requestRelease("P", who("N"), EARMARK / 4));
        (, ITicketBroker.ReserveInfo memory reserve) = broker.getSenderInfo(who("P"));
        expectEq(reserve.fundsRemaining, EARMARK - EARMARK / 4 + EARMARK / 2, "fundsRemaining");
        expectEq(broker.claimableReserve(who("P"), who("M")), EARMARK / 2, "M's earmark");
    }

    // --- helpers -----------------------------------------------------------

    function funded() internal {
        register("N");
        ok(fund("P", DEPOSIT, who("N"), EARMARK));
    }

    function redeemFace(uint256 face) internal {
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) =
            ticket("t1", "P", who("N"), clock.currentRound(), face);
        ok(redeem(t, sig, rand));
    }

    /// What the node is shown: the deposit, and N's claimable earmark.
    function shown(uint256 deposit, uint256 claimable) internal view {
        (ITicketBroker.Sender memory s,) = broker.getSenderInfo(who("P"));
        expectEq(s.deposit, deposit, "deposit shown");
        expectEq(broker.claimableReserve(who("P"), who("N")), claimable, "claimable shown");
    }

    function pendingIs(address node, uint256 pending, uint256 readyRound) internal view {
        (, uint256 p, uint256 r) = broker.earmarkOf(who("P"), node);
        expectEq(p, pending, "pending");
        expectEq(r, readyRound, "ready round");
    }

    function balanceIs(address node, uint256 amount) internal view {
        (uint256 a,,) = broker.earmarkOf(who("P"), node);
        expectEq(a, amount, "balance");
    }

    /// The Broker's USDC equals P's deposit and earmark for N plus the
    /// network share.
    function holds() internal view {
        (uint256 d,,) = broker.earmarkOf(who("P"), address(0));
        (uint256 e,,) = broker.earmarkOf(who("P"), who("N"));
        expectEq(usdc.balanceOf(address(broker)), d + e + broker.networkHeld(), "Broker USDC");
    }

    function ok(bytes memory ret) internal pure {
        require(ret.length == 0, string.concat("refused with ", code(ret)));
    }

    function refused(bytes memory ret, string memory want) internal pure {
        require(eq(code(ret), want), string.concat("want ", want, ", got ", ret.length == 0 ? "success" : code(ret)));
    }
}
