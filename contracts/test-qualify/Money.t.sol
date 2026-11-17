// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "../test/Vm.sol";
import {Handler} from "./Handler.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";
import {MockUSDC} from "../src/money/MockUSDC.sol";

/// The money invariants of stage 2, checked after every step of long
/// random sequences that the Handler drives through the real contracts.
/// Each names the rule it guards. They run only under the `qualify`
/// profile (contracts/qualify), never in contracts/check.
contract MoneyInvariants {
    struct FuzzSelector {
        address addr;
        bytes4[] selectors;
    }

    Handler internal h;
    ITicketBroker internal broker;
    MockUSDC internal usdc;

    function setUp() external {
        h = new Handler();
        broker = ITicketBroker(h.brokerAddress());
        usdc = MockUSDC(h.usdcAddress());
    }

    /// Only the Handler's steps are run; nobody calls the contracts
    /// around it.
    function targetContracts() external view returns (address[] memory out) {
        out = new address[](1);
        out[0] = address(h);
    }

    function targetSelectors() external view returns (FuzzSelector[] memory out) {
        bytes4[] memory s = new bytes4[](8);
        s[0] = Handler.registerNode.selector;
        s[1] = Handler.setNodePayout.selector;
        s[2] = Handler.fundAccount.selector;
        s[3] = Handler.askRelease.selector;
        s[4] = Handler.takeRelease.selector;
        s[5] = Handler.redeemNew.selector;
        s[6] = Handler.redeemAgain.selector;
        s[7] = Handler.nextRound.selector;
        out = new FuzzSelector[](1);
        out[0] = FuzzSelector(address(h), s);
    }

    // --- the invariants ---------------------------------------------------

    /// R-21 (and R-03 for the network share): the USDC the Broker holds
    /// equals every payer's deposit plus every earmark plus the network
    /// share, as the Broker itself reports them. Nothing is created and
    /// nothing is stranded inside money/.
    function invariant_R21_broker_holds_exactly_the_balances_and_network_share() external view {
        uint256 sum = broker.networkHeld();
        for (uint256 i = 0; i < h.trackedCount(); i++) {
            for (uint256 j = 0; j < h.slotCount(); j++) {
                (uint256 a,,) = broker.earmarkOf(h.tracked(i), h.slot(j));
                sum += a;
            }
        }
        eqOr(usdc.balanceOf(address(broker)), sum, "R-21: Broker USDC vs deposits + earmarks + network share");
    }

    /// R-21, R-03, R-22: what the Broker holds is what payers funded less
    /// what it paid out to nodes and released to payers; no USDC exists
    /// that was not minted to a payer.
    function invariant_R21_usdc_in_equals_usdc_out_plus_held() external view {
        eqOr(
            usdc.balanceOf(address(broker)),
            h.funded() - h.released() - h.paidToNodes(),
            "R-21: Broker USDC vs funded - released - paid to nodes"
        );
        eqOr(usdc.totalSupply(), h.minted(), "MockUSDC supply vs minted");
    }

    /// R-03 with R-01 and D-28: every node's half went to the payout
    /// address in force at its redemption, every release to its payer,
    /// and every address holds exactly what the rules sent it.
    function invariant_R03_R22_usdc_that_left_went_where_the_rules_say() external view {
        for (uint256 i = 0; i < h.trackedCount(); i++) {
            address a = h.tracked(i);
            eqAt(usdc.balanceOf(a), h.walletShould(a), "R-03/R-22: USDC held by tracked address", i);
        }
    }

    /// R-04: a ticket that paid is used, and is never paid again; a ticket
    /// refused for nothing to pay stays unused.
    function invariant_R04_no_ticket_paid_twice() external view {
        eqOr(h.paidTwice(), 0, "R-04: tickets paid twice");
        for (uint256 i = 0; i < h.keptCount(); i++) {
            (bytes32 hash, bool paid) = h.keptTicket(i);
            if (broker.usedTickets(hash) != paid) {
                revert(string.concat("R-04: kept ticket ", vm.toString(i), paid ? " paid but not used" : " used but never paid"));
            }
        }
    }

    /// R-21, R-22, R-04: every deposit and earmark, its pending release
    /// and the round it is ready, as the rules compute them step by step.
    function invariant_R21_R22_each_balance_follows_the_rules() external view {
        for (uint256 i = 0; i < h.trackedCount(); i++) {
            for (uint256 j = 0; j < h.slotCount(); j++) {
                (address p, address n) = (h.tracked(i), h.slot(j));
                (uint256 a, uint256 pe, uint256 r) = broker.earmarkOf(p, n);
                (uint256 ma, uint256 mp, uint256 mr) = h.modelOf(p, n);
                eqAt(a, ma, "R-21: amount of payer, slot*10+payer", j * 10 + i);
                eqAt(pe, mp, "R-22: pending of payer, slot*10+payer", j * 10 + i);
                eqAt(r, mr, "R-22: ready round of payer, slot*10+payer", j * 10 + i);
            }
        }
    }

    /// A payer's balances never go below zero or above what it put in:
    /// together they are exactly what it funded, less what it released and
    /// what its tickets paid (R-21, R-22, R-04); nothing pending is above
    /// its balance, and a cleared request has no ready round (R-22).
    function invariant_payer_never_below_zero_or_above_what_it_put_in() external view {
        for (uint256 i = 0; i < h.trackedCount(); i++) {
            address p = h.tracked(i);
            uint256 held;
            for (uint256 j = 0; j < h.slotCount(); j++) {
                (uint256 a, uint256 pe, uint256 r) = broker.earmarkOf(p, h.slot(j));
                if (pe > a) revert(string.concat("R-22: pending above balance, payer ", vm.toString(i)));
                if (pe == 0 && r != 0) revert(string.concat("R-22: cleared request keeps a round, payer ", vm.toString(i)));
                held += a;
            }
            uint256 putIn = h.fundedBy(p) - h.releasedTo(p);
            if (held > putIn) revert(string.concat("payer ", vm.toString(i), " holds more than it put in"));
            eqAt(held, putIn - h.paidFrom(p), "payer holds funded - released - paid", i);
        }
    }

    /// R-21, R-22, R-23: a node is shown the deposit less its pending
    /// release, its own earmark less its pending release, and the signer
    /// the sum of every earmark less its pending release.
    function invariant_R21_R23_nodes_are_shown_balances_less_pending() external view {
        for (uint256 i = 0; i < h.trackedCount(); i++) {
            address p = h.tracked(i);
            (ITicketBroker.Sender memory s, ITicketBroker.ReserveInfo memory r) = broker.getSenderInfo(p);
            (uint256 da, uint256 dp,) = h.modelOf(p, address(0));
            eqAt(s.deposit, da - dp, "R-21: deposit shown, payer", i);
            uint256 sum;
            for (uint256 j = 1; j < h.slotCount(); j++) {
                (uint256 ea, uint256 ep,) = h.modelOf(p, h.slot(j));
                sum += ea - ep;
                eqAt(broker.claimableReserve(p, h.slot(j)), ea - ep, "R-21: claimable, slot*10+payer", j * 10 + i);
            }
            eqOr(broker.claimableReserve(p, address(0)), 0, "R-21: claimable for no node");
            eqAt(r.fundsRemaining, sum, "R-23: fundsRemaining, payer", i);
        }
    }

    /// R-03: the network share is each gross less the node's rounded-down
    /// half, held in the Broker.
    function invariant_R03_split_and_network_share() external view {
        eqOr(broker.networkHeld(), h.paidToNetwork(), "R-03: network share held");
    }

    /// R-04: each fee is counted, for its node, in the round it was
    /// redeemed.
    /// Every round's total is checked; a round's per-node fees where
    /// either total is above 0.
    function invariant_R04_fees_counted_in_the_redemption_round() external view {
        // The clock is read through the Handler's deployment.
        uint256 current = h.currentRound();
        for (uint256 r = 1; r <= current; r++) {
            uint256 total = broker.totalFees(r);
            eqAt(total, h.feesShould(r), "R-04: total fees, round", r);
            if (total == 0) continue;
            for (uint256 i = 0; i < h.trackedCount(); i++) {
                address n = h.tracked(i);
                eqAt(broker.feesOf(r, n), h.feesOfShould(r, n), "R-04: fees of node, round*10+node", r * 10 + i);
            }
        }
    }

    /// R-01, R-04, R-21, R-22: every step was accepted or refused as the
    /// rules say, with a code the rules give.
    function invariant_every_step_has_the_outcome_the_rules_give() external view {
        if (h.mismatches() > 0) revert(h.firstMismatch());
    }

    function eqAt(uint256 got, uint256 want, string memory what, uint256 at) internal pure {
        if (got != want) eqOr(got, want, string.concat(what, " ", vm.toString(at)));
    }

    function eqOr(uint256 got, uint256 want, string memory what) internal pure {
        if (got != want) revert(string.concat(what, ": got ", vm.toString(got), ", want ", vm.toString(want)));
    }
}
