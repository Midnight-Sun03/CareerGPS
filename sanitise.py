import sqlite3

c = sqlite3.connect("export_db.sqlite3")
cur = c.cursor()

targets = [
    ("auth_user", ["email","username","first_name","last_name","password"]),
    ("core_profile", ["phone","institution","location","custom_skills"]),
    ("core_education", ["institution","description"]),
    ("core_workexperience", ["organisation","description"]),
    ("core_project", ["description"]),
    ("interview_question", ["audio_file","raw_transcript","corrected_transcript","answer_text","evaluation"]),
    ("interview_interview", ["feedback_data"]),
]

for table, cols in targets:
    for col in cols:
        try:
            cur.execute(f"UPDATE {table} SET {col} = 'redacted' WHERE {col} IS NOT NULL AND {col} != ''")
            print(f"scrubbed {table}.{col}: {cur.rowcount}")
        except Exception as e:
            print(f"skipped {table}.{col}: {e}")

c.commit()
cur.execute("VACUUM")
c.commit()
c.close()
print("done")
