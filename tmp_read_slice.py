import sys

path = sys.argv[1]
start = int(sys.argv[2])
end = int(sys.argv[3]) if len(sys.argv) > 3 else start + 20

with open(path, encoding='utf-8') as f:
    lines = f.read().splitlines()

print('total lines:', len(lines))
for i in range(start - 1, min(end, len(lines))):
    print('{:4d} {}'.format(i + 1, lines[i]))
