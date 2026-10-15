import sqlite3

c = sqlite3.connect("export_db.sqlite3")
cur = c.cursor()

try:
    cur.execute("UPDATE interview_interview SET feedback_data = '{}' WHERE feedback_data IS NOT NULL")
    print("scrubbed interview_interview.feedback_data:", cur.rowcount)
except Exception as e:
    print("skipped feedback_data:", e)

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%interview%'")
interview_tables = [r[0] for r in cur.fetchall()]
print("interview tables found:", interview_tables)

text_fields = ["audio_file","raw_transcript","corrected_transcript","answer_text",
               "evaluation","transcript","provisional_transcript"]
for t in interview_tables:
    for col in text_fields:
        try:
            cur.execute(f"UPDATE {t} SET {col} = NULL WHERE {col} IS NOT NULL")
            print(f"scrubbed {t}.{col}: {cur.rowcount}")
        except Exception:
            pass

for t in ["applications_generateddocument","applications_submitteddocument"]:
    for col in ["file","content","rendered_content"]:
        try:
            cur.execute(f"UPDATE {t} SET {col} = NULL WHERE {col} IS NOT NULL")
            print(f"scrubbed {t}.{col}: {cur.rowcount}")
        except Exception:
            pass

c.commit()
c.close()
print("done")
