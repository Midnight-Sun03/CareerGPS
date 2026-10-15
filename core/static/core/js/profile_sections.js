document.addEventListener("DOMContentLoaded", () => {
  const addInput = document.querySelector(".add-entry input[name$='entered_name']");
  if (addInput && document.getElementById("skill-suggestions")) {
    addInput.setAttribute("list", "skill-suggestions");
  }
  if (addInput && document.getElementById("interest-suggestions")) {
    addInput.setAttribute("list", "interest-suggestions");
  }
});
