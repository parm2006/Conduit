"""Interchangeable local digests; fixed wire/security formats stay explicit."""
from blake3 import blake3


def new_hasher(data=b''):
    return blake3(data)
