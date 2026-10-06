from game_brain.dashboard.framepack import changed_tiles, pack_frame

def test_only_changed_tile_is_packed():
    previous = bytes(240 * 160 * 4)
    current = bytearray(previous)
    current[0:3] = b"\\xff\\x00\\x00"
    tiles = changed_tiles(previous, bytes(current))
    assert len(tiles) == 1
    assert tiles[0][:2] == (0, 0)
    packed = pack_frame(7, tiles)
    assert packed.startswith(b"GBF1")
