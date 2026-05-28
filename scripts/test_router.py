import sys
sys.path.insert(0, ".")
from src.mypcc.router import is_personal_query

tests = [
    # Vietnamese
    ("tình trạng payment của tôi như thế nào?", True),
    ("cho tôi hỏi tình trạng payment của tôi", True),
    ("tôi học môn gì kỳ này?", True),
    ("cố vấn của tôi là ai?", True),
    ("học phí của tôi còn bao nhiêu?", True),
    ("đăng ký môn học kỳ sau khi nào?", True),
    ("thông tin trường pcc như thế nào?", False),
    # English — the failing case
    ("do you know what currently term that i am attending now and what are those classes?", True),
    ("what term am i in right now?", True),
    ("what classes am i attending this semester?", True),
    ("i am currently enrolled in what courses?", True),
    # English — financial
    ("my payment status", True),
    ("how much do I owe?", True),
    ("What courses am I taking?", True),
    # English — should NOT trigger
    ("What are F1 visa requirements?", False),
    ("What is the tuition for international students?", False),
    ("What is the class schedule for CS?", False),
]

all_ok = True
for q, expected in tests:
    result = is_personal_query(q)
    status = "OK" if result == expected else "FAIL"
    if result != expected:
        all_ok = False
    print(f"[{status}] is_personal={result} | {q}")

print()
print("All OK!" if all_ok else "Some tests FAILED")
