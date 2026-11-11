"""Small, bounded protobuf wire reader; no recursive schema guessing."""


class ProtobufDecodeError(ValueError):
    pass


def read_varint(payload, offset):
    value = 0
    for index in range(10):
        if offset >= len(payload):
            raise ProtobufDecodeError('truncated varint')
        byte = payload[offset]
        offset += 1
        if index == 9 and byte > 1:
            raise ProtobufDecodeError('varint exceeds uint64')
        value |= (byte & 0x7f) << (index * 7)
        if byte < 0x80:
            return value, offset
    raise ProtobufDecodeError('varint exceeds 10 bytes')


def fields(payload, *, max_fields=10000):
    """Yield wire fields, rejecting truncation rather than crossing messages."""
    if not isinstance(payload, bytes):
        raise ProtobufDecodeError('protobuf payload must be bytes')
    offset = count = 0
    while offset < len(payload):
        count += 1
        if count > max_fields:
            raise ProtobufDecodeError('too many protobuf fields')
        key, offset = read_varint(payload, offset)
        number, wire = key >> 3, key & 7
        if not 0 < number < 2**29:
            raise ProtobufDecodeError('invalid protobuf field number')
        if wire == 0:
            value, offset = read_varint(payload, offset)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, offset = read_varint(payload, offset)
            else:
                length = 8 if wire == 1 else 4
            if offset + length > len(payload):
                raise ProtobufDecodeError('truncated protobuf field')
            value = payload[offset:offset + length]
            offset += length
        else:
            raise ProtobufDecodeError('unsupported protobuf wire type')
        yield number, wire, value


def singular(decoded, number, wire, *, required=True):
    matches = [(candidate_wire, value) for candidate, candidate_wire, value in decoded if candidate == number]
    if not matches and not required:
        return None
    if len(matches) != 1 or matches[0][0] != wire:
        raise ProtobufDecodeError('missing, repeated or mistyped singular field')
    return matches[0][1]
