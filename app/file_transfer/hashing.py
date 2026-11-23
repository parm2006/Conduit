"""File-content digests; authentication and certificate hashes are separate."""
import hashlib

from blake3 import blake3


SUPPORTED_HASH_ALGORITHMS = ("blake3", "sha256")


class UnsupportedHashAlgorithm(ValueError):
    pass


def validate_hash_algorithm(algorithm):
    if not isinstance(algorithm, str) or algorithm not in SUPPORTED_HASH_ALGORITHMS:
        raise UnsupportedHashAlgorithm("unsupported file hash algorithm")
    return algorithm


def select_hash_algorithm(capabilities):
    # Missing capabilities identify a legacy peer, whose wire format is SHA-256.
    if capabilities is None:
        return "sha256"
    if (not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 8
            or any(not isinstance(value, str) for value in capabilities)):
        raise UnsupportedHashAlgorithm("invalid file hash capabilities")
    for algorithm in SUPPORTED_HASH_ALGORITHMS:
        if algorithm in capabilities:
            return algorithm
    raise UnsupportedHashAlgorithm("no supported file hash algorithm")


def new_hasher(algorithm):
    validate_hash_algorithm(algorithm)
    # Incremental staging uses small chunks; parallel dispatch is slower there.
    return blake3(max_threads=1) if algorithm == "blake3" else hashlib.sha256()
