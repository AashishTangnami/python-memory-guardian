"""Lists vs generators: the same answers with very different peak memory.

The streaming versions are generator functions: each `yield` hands one value to
the next stage and pauses, so only one value exists at a time. Profile in
precise mode and compare the memory labels on the eager and streaming functions.
Static diagnostics flag the eager patterns before you run anything.
Standard library only.
"""
import os
import tempfile

N = 2_000_000


def make_log(path, lines=400_000):
    with open(path, "w") as f:
        for i in range(lines):
            f.write(f"{i},user{i % 1000},{i * 7 % 997}\n")


def eager_total(n):
    squares = [i * i for i in range(n)]              # the whole list is alive at once
    evens = [x for x in squares if x % 2 == 0]       # ...and a second list next to it
    return sum(evens)


def squares(n):
    for i in range(n):
        yield i * i                                  # one value at a time


def evens(values):
    for x in values:
        if x % 2 == 0:
            yield x


def streaming_total(n):
    return sum(evens(squares(n)))                    # sum pulls values through both stages


def eager_log_sum(path):
    with open(path) as f:
        lines = f.readlines()                        # every line as a separate str, all at once
    return sum(int(line.rsplit(",", 1)[1]) for line in lines)


def read_amounts(path):
    with open(path) as f:
        for line in f:                               # the file object reads line by line
            yield int(line.rsplit(",", 1)[1])


def streaming_log_sum(path):
    return sum(read_amounts(path))


def report_by_concat(n):
    out = ""
    for i in range(n):
        out += f"row {i}\n"                          # may copy the growing string
    return len(out)


def report_by_join(n):
    return len("".join(f"row {i}\n" for i in range(n)))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "events.csv")
        make_log(log)
        assert eager_total(N) == streaming_total(N)
        assert eager_log_sum(log) == streaming_log_sum(log)
        assert report_by_concat(200_000) == report_by_join(200_000)
    print("generators: eager and streaming results match")
