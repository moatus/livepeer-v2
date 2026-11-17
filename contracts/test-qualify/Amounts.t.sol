// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Stage2} from "../test/Stage2.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// Fuzz runs over the whole range of amounts a payer can fund, which the
/// shared examples (all small) and the invariant runs (bounded, so their
/// sums stay exact) do not reach. They run only under the `qualify`
/// profile (contracts/qualify).
contract AmountsFuzz is Stage2 {
    uint256 internal constant ONE = 1_000_000;
    uint256 internal nodeCut;

    function setUp() external {
        loadVectors();
        nodeCut = param("node_cut_millionths");
        deploy();
        register("N1");
        register("N2");
    }

    /// R-03 and R-19: for any amount, the node is paid
    /// floor(gross x node cut / 10^6), the network keeps the rest, and the
    /// two sum to the gross; no amount a payer can fund makes the split
    /// fail. The expected half is computed as (g div 10^6) x cut +
    /// floor((g mod 10^6) x cut / 10^6), which equals the floor exactly
    /// and cannot overflow.
    function testFuzz_R03_split_holds_for_any_amount(uint256 face) external {
        face = 1 + face % (type(uint256).max / 2);
        require(fund("P", face, address(0), 0).length == 0, "fund refused");
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket("t", "P", who("N1"), 1, face);
        bytes memory refused = redeem(t, sig, rand);
        if (refused.length > 0) revert(string.concat("ticket refused: ", code(refused)));
        uint256 toNode = face / ONE * nodeCut + face % ONE * nodeCut / ONE;
        expectEq(usdc.balanceOf(who("N1")), toNode, "R-03: node half");
        expectEq(broker.networkHeld(), face - toNode, "R-03: network share");
        expectEq(usdc.balanceOf(address(broker)), face - toNode, "R-21: Broker USDC");
        expectEq(broker.totalFees(1), face, "R-04: fee is the gross");
    }

    /// R-04 and R-21: a ticket pays min(face, deposit + that node's
    /// earmark), deposit first; another node's earmark is never drawn; a
    /// ticket that would move 0 is refused with NOTHING_TO_PAY and stays
    /// unused.
    function testFuzz_R04_pays_min_of_face_and_deposit_plus_earmark(
        uint256 dep,
        uint256 mark,
        uint256 other,
        uint256 face
    ) external {
        (dep, mark, other, face) = (dep >> 4, mark >> 4, other >> 4, face >> 2);
        require(fund("P", dep, who("N1"), mark).length == 0, "fund N1 refused");
        require(fund("P", 0, who("N2"), other).length == 0, "fund N2 refused");
        uint256 fromDeposit = dep < face ? dep : face;
        uint256 fromEarmark = mark < face - fromDeposit ? mark : face - fromDeposit;
        redeemAndCheckUse(face, fromDeposit + fromEarmark);
        (uint256 d,,) = broker.earmarkOf(who("P"), address(0));
        (uint256 e1,,) = broker.earmarkOf(who("P"), who("N1"));
        (uint256 e2,,) = broker.earmarkOf(who("P"), who("N2"));
        expectEq(d, dep - fromDeposit, "R-04: deposit left");
        expectEq(e1, mark - fromEarmark, "R-04: N1 earmark left");
        expectEq(e2, other, "R-21: N2 earmark untouched");
        expectEq(broker.totalFees(1), fromDeposit + fromEarmark, "R-04: fee is what moved");
        expectEq(usdc.balanceOf(address(broker)), d + e1 + e2 + broker.networkHeld(), "R-21: Broker USDC");
    }

    function redeemAndCheckUse(uint256 face, uint256 paid) internal {
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket("t", "P", who("N1"), 1, face);
        bytes memory refused = redeem(t, sig, rand);
        if (paid == 0) {
            require(eq(code(refused), "NOTHING_TO_PAY"), string.concat("want NOTHING_TO_PAY, got ", code(refused)));
            require(!broker.usedTickets(ticketHash(t)), "R-04: refused ticket marked used");
        } else {
            if (refused.length > 0) revert(string.concat("ticket refused: ", code(refused)));
            require(broker.usedTickets(ticketHash(t)), "R-04: paid ticket not used");
        }
    }
}
