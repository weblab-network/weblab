'use strict';
const copyButton = document.querySelector('#copy-command');
const copyStatus = document.querySelector('#copy-status');
if (navigator.clipboard && window.isSecureContext) {
  copyButton.hidden = false;
  copyButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(document.querySelector('#clone-command').textContent);
      copyStatus.textContent = 'Command copied.';
    } catch {
      copyStatus.textContent = 'Select the command above to copy it manually.';
    }
  });
}
