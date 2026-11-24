// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IBurnableTestLPT} from "../../interfaces/ITestLPT.sol";

/// Stand-in for LPT: an ERC-20 with 18 decimals. The constructor creates the
/// genesis supply for the deployer, outside every round's issuance (R-09);
/// after that the one address that may mint is Claims, fixed here at
/// deployment and never changed. Any holder may burn from its own balance
/// (R-16), which lowers totalSupply. The deployment manifest names it as a
/// stand-in.
contract TestLPT is IBurnableTestLPT {
    string public constant name = "Test LPT";
    string public constant symbol = "tLPT";
    uint8 public constant decimals = 18;

    /// The one minter, Claims. It is deployed after this token, so the
    /// deployer passes the address Claims will have.
    address private immutable claims;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(address claims_, uint256 genesisSupply) {
        require(claims_ != address(0), "zero address");
        claims = claims_;
        totalSupply = genesisSupply;
        balanceOf[msg.sender] = genesisSupply;
        emit Transfer(address(0), msg.sender, genesisSupply);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _move(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            require(allowed >= amount, "allowance too low");
            allowance[from][msg.sender] = allowed - amount;
        }
        _move(from, to, amount);
        return true;
    }

    /// Only Claims. Every other caller, the deployer included, is refused.
    function mint(address to, uint256 amount) external {
        if (msg.sender != claims) revert NOT_AUTHORIZED();
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    /// Any holder, from its own balance: the balance and totalSupply fall
    /// by amount (R-16). Refused as a transfer is, "balance too low".
    function burn(uint256 amount) external {
        require(balanceOf[msg.sender] >= amount, "balance too low");
        balanceOf[msg.sender] -= amount;
        totalSupply -= amount;
        emit Transfer(msg.sender, address(0), amount);
    }

    function _move(address from, address to, uint256 amount) private {
        require(balanceOf[from] >= amount, "balance too low");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}
