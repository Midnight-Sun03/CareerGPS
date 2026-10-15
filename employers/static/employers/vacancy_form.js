document.getElementById('add-requirement').addEventListener('click', function () {
  const total = document.getElementById('id_requirements-TOTAL_FORMS');
  const index = Number(total.value);
  if (index >= 30) return;
  const fragment = document.getElementById('empty-requirement').innerHTML.replaceAll('__prefix__', String(index));
  document.getElementById('requirement-rows').insertAdjacentHTML('beforeend', fragment);
  total.value = index + 1;
  if (index + 1 >= 30) this.disabled = true;
});
