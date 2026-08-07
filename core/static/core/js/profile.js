
     document.addEventListener("DOMContentLoaded", () => {
        const fieldIds = ["userName", "userQual", "userInst", "userSkills", "userLoc", "userStatement"];
        const editBtn  = document.getElementById("editProfileBtn");
        const signOutBtn = document.getElementById("signOutBtn");

        // ── Edit / Save toggle ──
        editBtn.addEventListener("click", () => {
          const isEditing = editBtn.dataset.mode === "editing";

          if (!isEditing) {
            // Switch to edit mode
            fieldIds.forEach(id => {
              const el = document.getElementById(id);
              const value = el.innerText;
              const isStatement = id === "userStatement";
              el.innerHTML = isStatement
                ? `<textarea class="edit-input" rows="3" style="resize:vertical">${value}</textarea>`
                : `<input class="edit-input" type="text" value="${value}" />`;
            });

            editBtn.innerHTML = '<i class="fa-solid fa-check"></i> Save Changes';
            editBtn.dataset.mode = "editing";

          } else {
            // Save
            fieldIds.forEach(id => {
              const el = document.getElementById(id);
              const input = el.querySelector("input, textarea");
              const newVal = input ? input.value : el.innerText;
              el.innerText = newVal;
            });

            editBtn.innerHTML = '<i class="fa-solid fa-pen"></i> Edit Profile';
            editBtn.dataset.mode = "";
          }
        });

          const pct = (filled / checkIds.length) * 100;
          document.getElementById("progressFill").style.width = pct + "%";
          document.getElementById("progressPct").textContent = Math.round(pct) + "%";

          const hint = document.getElementById("progressHint");
          if (pct < 100) {
            hint.textContent = "Upload your CV to improve matches";
            hint.style.color = "#aaa";
          } else {
            hint.textContent = "✓ Profile complete — you're all set!";
            hint.style.color = "#e85d04";
          }
        }
      });

