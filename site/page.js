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

document.querySelectorAll('.video-stage').forEach(stage => {
  const video = stage.querySelector('video');
  const playButton = stage.querySelector('.video-play');
  const status = document.getElementById(video.dataset.status);
  const showError = () => {
    playButton.hidden = true;
    status.textContent = 'Playback could not start. Use the player controls or the video links below.';
    status.hidden = false;
  };
  playButton.hidden = false;
  video.addEventListener('playing', () => {
    playButton.hidden = true;
    status.hidden = true;
  });
  video.addEventListener('error', showError);
  video.querySelector('source:last-of-type').addEventListener('error', showError);
  playButton.addEventListener('click', async () => {
    playButton.hidden = true;
    status.hidden = true;
    try {
      await video.play();
    } catch {
      showError();
    }
  });
});

const walkthrough = document.querySelector('#walkthrough');
if (walkthrough) {
  const openLinkedWalkthrough = () => {
    if (location.hash === '#walkthrough') walkthrough.open = true;
  };
  openLinkedWalkthrough();
  window.addEventListener('hashchange', openLinkedWalkthrough);
  walkthrough.addEventListener('toggle', () => {
    if (!walkthrough.open) walkthrough.querySelector('video').pause();
  });
}

// Reveal on request to reduce simple address harvesting; this is not bot protection.
const showEmail = document.querySelector('#show-email');
const contactEmail = document.querySelector('#contact-email');
if (showEmail && contactEmail) {
  showEmail.hidden = false;
  showEmail.addEventListener('click', () => {
    const decode = part => [...part].reverse().join('');
    const address = decode('ved') + String.fromCharCode(64) +
      [decode('balbew'), decode('krowten')].join('.');
    contactEmail.textContent = address;
    contactEmail.href = 'mailto:' + address;
    contactEmail.hidden = false;
    showEmail.hidden = true;
    contactEmail.focus();
  }, { once: true });
}
