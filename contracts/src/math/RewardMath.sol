// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The reward maths of rulebook `october-main-text-v2`: the emission
/// ceiling (R-05), its three shares (R-07, R-10), a node's reward (R-08)
/// and the split of a reward between a node and its holders (R-24).
///
/// Every function is `pure`: it takes everything it needs as arguments, and
/// the compiler refuses one that reads or changes state, so nothing here can
/// mint, pay or look anything up. Each amount is one division, rounded down,
/// with no share or intermediate amount stored first (R-19). A product too
/// large for 256 bits reverts; it is outside R-19.
library RewardMath {
    /// A whole, in millionths: the emission shares, a reward cut and a score.
    uint256 internal constant ONE = 1_000_000;
    /// EmissionBase (R-06): 1,000 LPT a round, a test value.
    uint256 internal constant EMISSION_BASE = 1_000_000_000_000_000_000_000;
    /// EmissionHalfLife (R-05), in rounds.
    uint256 internal constant HALF_LIFE = 840;
    /// The emission shares (R-07), in millionths of the ceiling.
    uint256 internal constant NODE_SHARE = 940_000;
    uint256 internal constant VALIDATOR_SHARE = 10_000;
    uint256 internal constant TREASURY_SHARE = 50_000;

    /// K_i is 2^(-2^i / 840) in 128-bit fixed point, rounded down: the
    /// largest integer k with k^840 <= 2^(128 x 840 - 2^i). Ten of them cover
    /// every residue below 840 < 2^10.
    uint256 private constant K0 = 0xffc9f19149c4d5857c4f930d0fce6270;
    uint256 private constant K1 = 0xff93ee8cab0ed7d07984ca4b5c6648ef;
    uint256 private constant K2 = 0xff280ab8109de0584f529ca71915cba9;
    uint256 private constant K3 = 0xfe50cb9e0b129bea432ce0724e05c8e5;
    uint256 private constant K4 = 0xfca46d8d82b3e0277e6f9fe39f4b646a;
    uint256 private constant K5 = 0xf954214b2583674b5d477d04aa47ce85;
    uint256 private constant K6 = 0xf2d4c46a0c9eb2e746097ba0de33c00e;
    uint256 private constant K7 = 0xe656f42e6633805705737b5c6cc457e9;
    uint256 private constant K8 = 0xcf405c4c57b9b071a838418cc98dbbc9;
    uint256 private constant K9 = 0xa7c92571bd511e01b20147248313f0a3;

    /// C(r), R-05: the largest integer c, in LPT wei, with
    /// c^840 x 2^r <= EmissionBase^840, for the emission index r = R - 1.
    ///
    /// With r = 840k + j, C(r) = floor(C(j) / 2^k) (R-05), so only the 840
    /// residues j are computed: 2^(-j/840) is the product of the K_i of j's
    /// bits, each product rounded down, as PRBMath's exp2 builds 2^x from
    /// the bits of x. Every step rounds down, so the result is never above
    /// the exact value and falls short of it by far less than 10^-15 wei;
    /// contracts/check tests all 840 residues against the exact inequality,
    /// and each lands on the exact floor. j = 0 is exact.
    function ceiling(uint256 r) internal pure returns (uint256) {
        uint256 j = r % HALF_LIFE;
        uint256 x = 1 << 128;
        if (j & 0x001 != 0) x = (x * K0) >> 128;
        if (j & 0x002 != 0) x = (x * K1) >> 128;
        if (j & 0x004 != 0) x = (x * K2) >> 128;
        if (j & 0x008 != 0) x = (x * K3) >> 128;
        if (j & 0x010 != 0) x = (x * K4) >> 128;
        if (j & 0x020 != 0) x = (x * K5) >> 128;
        if (j & 0x040 != 0) x = (x * K6) >> 128;
        if (j & 0x080 != 0) x = (x * K7) >> 128;
        if (j & 0x100 != 0) x = (x * K8) >> 128;
        if (j & 0x200 != 0) x = (x * K9) >> 128;
        // A shift of 256 or more gives 0, as floor(C(j) / 2^k) is then.
        return ((EMISSION_BASE * x) >> 128) >> (r / HALF_LIFE);
    }

    /// A node's reward, R-08 and R-11: floor(c x 940,000 x a x score /
    /// (10^6 x b x 10^6)), where a/b is fee/totalFee when
    /// fee x totalStake <= stake x totalFee, and stake/totalStake otherwise.
    /// c is the earning round's ceiling. The shares are compared by
    /// cross-multiplying and meet the score inside the one division. A round
    /// with zero total fees or zero total stake pays no node. The caller
    /// refuses a score above 10^6 before it gets here (R-11).
    function nodeReward(uint256 c, uint256 fee, uint256 totalFee, uint256 stake, uint256 totalStake, uint256 score)
        internal
        pure
        returns (uint256)
    {
        if (totalFee == 0 || totalStake == 0) return 0;
        (uint256 a, uint256 b) = fee * totalStake <= stake * totalFee ? (fee, totalFee) : (stake, totalStake);
        return (c * NODE_SHARE * a * score) / (ONE * b * ONE);
    }

    /// The node's cut of a reward p, R-24: floor(p x cut / 10^6). The pot,
    /// p less the cut, goes to Claims for the node's holders.
    function cutOf(uint256 p, uint256 cut) internal pure returns (uint256) {
        return (p * cut) / ONE;
    }

    /// A holder's part of a pot, R-24: floor(pot x bond / stake), with the
    /// holder's bond and the node's stake at the start of the earning round.
    /// A node with no stake has no parts.
    function partOf(uint256 pot, uint256 bond, uint256 stake) internal pure returns (uint256) {
        if (stake == 0) return 0;
        return (pot * bond) / stake;
    }

    /// The validator amount of a round with ceiling c, R-10: floor(c x 10,000 / 10^6).
    function validatorsOf(uint256 c) internal pure returns (uint256) {
        return (c * VALIDATOR_SHARE) / ONE;
    }

    /// The treasury amount of a round with ceiling c, R-10: floor(c x 50,000 / 10^6).
    function treasuryOf(uint256 c) internal pure returns (uint256) {
        return (c * TREASURY_SHARE) / ONE;
    }
}
