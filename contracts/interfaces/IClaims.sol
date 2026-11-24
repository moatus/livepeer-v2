// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The one LPT minter (rulebook R-09, R-10, R-12, R-14, R-24). It mints each
/// claim and each round's validator and treasury amounts, and holds every
/// pot, of claims and of validator pay, until its holders withdraw their
/// parts. A part is paid only from its own pot, by transfer, never by a
/// mint; what the floors leave stays here and is never paid.
interface IClaims {
    /// Only an address the Registry's mayActFor allows for `node`, in rounds
    /// round + 7 to round + 28. Reads the score source's scoreOf(node) and
    /// mints the node's cut to payoutOf(node), read now, and the pot to
    /// Claims. A score of 0 pays 0 and closes the entitlement. Refused, the
    /// first that applies, with NOT_AUTHORIZED, NO_SUCH_ROUND for round 0,
    /// NOT_REGISTERED when `node` is not a registered node, CLAIM_TOO_EARLY,
    /// CLAIM_EXPIRED, ALREADY_CLAIMED or SCORE_OUT_OF_RANGE.
    function claim(address node, uint256 round) external;

    /// Transfers the caller's parts of `node`'s claims for `rounds` from
    /// Claims to the caller. Refused with NO_SUCH_ROUND when the list names
    /// round 0, whatever else it holds; with NOT_CLAIMED when a round is not
    /// claimed, and with ALREADY_CLAIMED when a round is withdrawn already
    /// or named twice; a refused list moves nothing.
    function withdrawParts(address node, uint256[] calldata rounds) external;

    /// Anyone, once `round` has ended, once. For each of membersAt(round),
    /// at most N: R-14's pay, its cut to payoutOf(member), read now, and its
    /// pot to Claims; the treasury amount and what the division leaves to
    /// the treasury. Refused with NO_SUCH_ROUND for round 0, with
    /// CLAIM_TOO_EARLY before `round` has ended, and with ALREADY_CLAIMED
    /// the second time.
    function mintRoundShares(uint256 round) external;

    /// Transfers the caller's parts of `node`'s validator pots for `rounds`
    /// from Claims to the caller. Refused with NO_SUCH_ROUND when the list
    /// names round 0, whatever else it holds; with NOT_CLAIMED when a round's
    /// shares are not minted, and with ALREADY_CLAIMED when a round is
    /// withdrawn already or named twice; a refused list moves nothing. A
    /// round in which `node` was no member withdraws as 0.
    function withdrawValidatorParts(address node, uint256[] calldata rounds) external;

    function entitlementOf(address node, uint256 round)
        external
        view
        returns (bool claimed, uint256 paid, uint256 pot);
    function partOf(address holder, address node, uint256 round)
        external
        view
        returns (uint256 amount, bool withdrawn);

    /// 0s when `node` was not a member of `round` or the shares are not minted.
    function validatorPayOf(address node, uint256 round)
        external
        view
        returns (uint256 paid, uint256 cut, uint256 pot);
    function validatorPartOf(address holder, address node, uint256 round)
        external
        view
        returns (uint256 amount, bool withdrawn);
    function sharesMinted(uint256 round) external view returns (bool);

    event Claimed(
        address indexed node, uint256 indexed round, address payout, uint256 score, uint256 paid, uint256 cut, uint256 pot
    );
    event PartsWithdrawn(address indexed holder, address indexed node, uint256[] rounds, uint256 amount);

    /// R-10's two amounts, whoever receives them.
    event RoundSharesMinted(uint256 indexed round, uint256 validators, uint256 treasury);

    /// One for each member.
    event ValidatorPaid(
        uint256 indexed round, address indexed node, address payout, uint256 paid, uint256 cut, uint256 pot
    );
    event ValidatorPartsWithdrawn(address indexed holder, address indexed node, uint256[] rounds, uint256 amount);

    error NOT_AUTHORIZED();
    error CLAIM_TOO_EARLY();
    error CLAIM_EXPIRED();
    error ALREADY_CLAIMED();
    error SCORE_OUT_OF_RANGE();
    error NOT_CLAIMED();
    error NO_SUCH_ROUND();
    error NOT_REGISTERED();
    /// Refused before any other refusal while R-27's LPT stop is in force.
    error STOPPED();
}
