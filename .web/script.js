function update_versions(elem) {
  old_versions = document.querySelectorAll("tr:has(td:empty)");
  for (i = 0; i < old_versions.length; ++i) { old_versions[i].style = (elem.checked ? "" : "display: none;"); }
}

