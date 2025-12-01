from edge_compat import random, unique_id


def _load_device_id(path: str = "device_id.txt") -> str:
    """フラッシュから device_id を読み込み。無ければ作成して保存。"""
    try:
        with open(path, "r") as f:
            did = f.read().strip()
            if did:
                return did
    except Exception:
        pass

    # 新規作成: machine.unique_id() があればそれをHEX化
    try:
        raw = unique_id()  # type: ignore
        did = "".join("{:02x}".format(b) for b in raw)
    except Exception:
        rnd = random.getrandbits(64)
        did = "edge-" + "{:016x}".format(rnd)

    try:
        with open(path, "w") as f:
            f.write(did)
    except Exception:
        pass
    return did


__all__ = ["_load_device_id"]
