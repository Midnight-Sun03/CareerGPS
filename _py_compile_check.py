import py_compile, os

files = [
    "D:/CG/core/models.py",
    "D:/CG/core/middleware.py",
    "D:/CG/core/services/queue_worker.py",
    "D:/CG/core/test_provisional_bridge.py",
    "D:/CG/core/test_queue_safety_net.py",
    "D:/CG/core/test_queue_worker.py",
    "D:/CG/core/test_target_auto_rebuild.py",
    "D:/CG/core/test_target_state_lifecycle.py",
    "D:/CG/settings.py",
    "D:/CG/CareerGPS_Rebuild/settings.py",
]

for f in files:
    if not os.path.exists(f):
        print("MISSING:", f)
        continue
    try:
        py_compile.compile(f, doraise=True)
        print("OK:", f)
    except Exception as e:
        print("FAIL:", f, "->", e)
