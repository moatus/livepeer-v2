// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The few forge cheatcodes the tests use, declared here so the project
/// needs no test library. Each is forge's own, at forge's fixed address.
interface Vm {
    struct Gas {
        uint64 gasLimit;
        uint64 gasTotalUsed;
        uint64 gasMemoryUsed;
        int64 gasRefunded;
        uint64 gasRemaining;
    }

    struct Log {
        bytes32[] topics;
        bytes data;
        address emitter;
    }

    struct DirEntry {
        string errorMessage;
        string path;
        uint64 depth;
        bool isDir;
        bool isSymlink;
    }

    function roll(uint256 blockNumber) external;
    function warp(uint256 timestamp) external;
    function recordLogs() external;
    function getRecordedLogs() external returns (Log[] memory logs);
    function prank(address sender) external;
    function addr(uint256 privateKey) external pure returns (address);
    function getNonce(address account) external view returns (uint64);
    function computeCreateAddress(address deployer, uint256 nonce) external pure returns (address);
    function sign(uint256 privateKey, bytes32 digest) external pure returns (uint8 v, bytes32 r, bytes32 s);
    function projectRoot() external view returns (string memory);
    function readFile(string calldata path) external view returns (string memory);
    function readDir(string calldata path, uint64 maxDepth) external view returns (DirEntry[] memory entries);
    function parseJson(string calldata json, string calldata key) external pure returns (bytes memory);
    function parseJsonString(string calldata json, string calldata key) external pure returns (string memory);
    function parseJsonBool(string calldata json, string calldata key) external pure returns (bool);
    function parseJsonKeys(string calldata json, string calldata key) external pure returns (string[] memory);
    function keyExistsJson(string calldata json, string calldata key) external view returns (bool);
    function parseUint(string calldata value) external pure returns (uint256);
    function toString(uint256 value) external pure returns (string memory);
    function toString(bytes calldata value) external pure returns (string memory);
    function toString(address value) external pure returns (string memory);
    function lastFrameGas() external view returns (Gas memory gas);
    function cool(address target) external;
    function snapshotState() external returns (uint256 snapshotId);
    function revertToState(uint256 snapshotId) external returns (bool success);
    function load(address target, bytes32 slot) external view returns (bytes32 data);
    function deal(address account, uint256 newBalance) external;
}

Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
