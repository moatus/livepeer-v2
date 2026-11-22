// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IBuybackValues} from "../../interfaces/IBuybackValues.sol";

/// The Buyback's values source in stage 4's first deployment (spec S-07,
/// 3.1): the same values for every round, set at construction and never
/// changed. Each is checked against D-47 (v)'s range when deployed; one
/// outside it is refused with the text "VALUE_OUT_OF_RANGE", the code
/// Parameters gives the same values. The pool is checked by the Buyback at
/// each use (R-16 rule 13), not here. S-10's Parameters replaces it.
contract FixedBuybackValues is IBuybackValues {
    address private immutable pool;
    uint256 private immutable burnPpm;
    uint256 private immutable twapSeconds;
    uint256 private immutable maxSlippagePpm;
    uint256 private immutable buyEveryRounds;

    constructor(
        address pool_,
        uint256 burnPpm_,
        uint256 twapSeconds_,
        uint256 maxSlippagePpm_,
        uint256 buyEveryRounds_
    ) {
        require(burnPpm_ == 1_000_000, "VALUE_OUT_OF_RANGE");
        require(twapSeconds_ >= 1800 && twapSeconds_ <= 7200, "VALUE_OUT_OF_RANGE");
        require(maxSlippagePpm_ >= 5000 && maxSlippagePpm_ <= 50_000, "VALUE_OUT_OF_RANGE");
        require(buyEveryRounds_ >= 1 && buyEveryRounds_ <= 840, "VALUE_OUT_OF_RANGE");
        pool = pool_;
        burnPpm = burnPpm_;
        twapSeconds = twapSeconds_;
        maxSlippagePpm = maxSlippagePpm_;
        buyEveryRounds = buyEveryRounds_;
    }

    function buybackValuesAt(uint256) external view returns (BuybackValues memory) {
        return BuybackValues(pool, burnPpm, twapSeconds, maxSlippagePpm, buyEveryRounds);
    }
}
