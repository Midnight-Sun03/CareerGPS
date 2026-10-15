import re

src = open("D:\\CG\\_pb.err", encoding="utf-8").read()
blocks = re.split(r"(?=^(?:FAIL|ERROR): )", src, flags=re.M)
for b in blocks:
    if b.startswith(("FAIL: test_page_request", "FAIL: test_target_change_enqueues", "FAIL: test_v2_failure")):
        print(b[:2200])
        print("#" * 60)
