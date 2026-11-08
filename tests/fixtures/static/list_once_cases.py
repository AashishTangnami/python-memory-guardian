"""memory-swell.list-once: a list built only to be iterated once. Expected diagnostics are marked per line."""


def produce(n):
    for i in range(n):
        yield i


def consume(items):
    total = 0
    for item in items:
        total += item
    return total


def consume_twice(items):
    return len(items) + sum(items)


def loop_once(n):
    values = list(produce(n))  # expect: memory-swell.list-once
    for v in values:
        print(v)


def comprehension_once(n):
    squares = [i * i for i in range(n)]  # expect: memory-swell.list-once
    return [s + 1 for s in squares]


def aggregate_once(n):
    kept = [i for i in produce(n) if i % 2]  # expect: memory-swell.list-once
    return sum(kept)


def into_iterating_function(n):
    rows = list(produce(n))  # expect: memory-swell.list-once
    return consume(rows)


def generator_argument(n):
    rows = list(i for i in range(n))  # expect: memory-swell.list-once
    return max(rows)


def enumerated(n):
    rows = list(produce(n))  # expect: memory-swell.list-once
    for i, r in enumerate(rows):
        print(i, r)


MODULE_ROWS = list(produce(3))  # expect: memory-swell.list-once
for _row in MODULE_ROWS:
    pass


# Not reported: the list is needed, or the use is not a single iteration.

def needs_len(n):
    rows = list(produce(n))
    return len(rows), sum(rows)


def indexed(n):
    rows = [i for i in range(n)]
    return rows[0]


def into_function_that_needs_a_list(n):
    rows = list(produce(n))
    return consume_twice(rows)


def rebound(n):
    rows = list(produce(n))
    rows = rows + [1]
    return sum(rows)


def closure(n):
    rows = list(produce(n))

    def inner():
        return sum(rows)
    return inner


def unpacked(n):
    rows = list(produce(n))
    print(*rows)


def annotated(n):
    rows: list = list(produce(n))
    for r in rows:
        print(r)


def chained(n):
    a = b = list(produce(n))
    return sum(a)


def attribute_target(holder, n):
    holder.rows = list(produce(n))
    for r in holder.rows:
        print(r)


def starred(n):
    rows = list(*[produce(n)])
    for r in rows:
        print(r)


def used_before(n):
    for r in []:
        print(rows)
    rows = list(produce(n))


def rebound_by_with(n):
    rows = list(produce(n))
    with open(__file__) as rows:
        pass


def keyword_name(n):
    rows = list(produce(n))
    return dict(rows=1)
