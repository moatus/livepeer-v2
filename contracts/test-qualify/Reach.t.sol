// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Handler} from "./Handler.sol";

/// The invariant runs mean something only if the Handler reaches every
/// outcome: tickets paid in full and in part, replays, every refusal, and
/// releases paid. This walks 800 pseudo-random steps and fails if any is
/// never reached. With -vv it prints how often each was.
contract HandlerReach {
    function test_handler_reaches_every_outcome() external {
        Handler h = new Handler();
        uint256 seed = 1;
        for (uint256 i = 0; i < 800; i++) {
            seed = uint256(keccak256(abi.encode(seed)));
            uint256 a = uint256(keccak256(abi.encode(seed, 1)));
            uint256 b = uint256(keccak256(abi.encode(seed, 2)));
            uint256 c = uint256(keccak256(abi.encode(seed, 3)));
            uint256 k = seed % 20;
            if (k == 0) h.registerNode(a);
            else if (k == 1) h.setNodePayout(a, b);
            else if (k < 6) h.fundAccount(a, b, c, seed >> 128);
            else if (k < 9) h.askRelease(a, b, c);
            else if (k < 11) h.takeRelease(a, b);
            else if (k < 16) h.redeemNew(a, b, c, seed >> 128);
            else if (k < 18) h.redeemAgain(a);
            else h.nextRound(a);
        }
        if (h.mismatches() > 0) revert(h.firstMismatch());
        string[17] memory outcomes = [
            "register",
            "setPayout",
            "fund",
            "requestRelease",
            "release",
            "redeemAgain",
            "nextRound",
            "paid",
            "paidPartly",
            "refused NOT_REGISTERED",
            "refused TICKET_USED",
            "refused TICKET_EXPIRED",
            "refused TICKET_INVALID",
            "refused NOTHING_TO_PAY",
            "refused AMOUNT_TOO_LARGE",
            "refused RELEASE_NOT_READY",
            "refused PAYOUT_INVALID"
        ];
        for (uint256 i = 0; i < outcomes.length; i++) {
            uint256 n = h.ran(outcomes[i]);
            log(outcomes[i], n);
            require(n > 0, string.concat("never reached: ", outcomes[i]));
        }
    }

    function log(string memory what, uint256 n) internal view {
        (bool ok,) = address(0x000000000000000000636F6e736F6c652e6c6f67).staticcall(
            abi.encodeWithSignature("log(string,uint256)", what, n)
        );
        ok;
    }
}
