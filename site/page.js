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

const overviewVideo = document.querySelector('.overview-video video');
const overviewPlay = document.querySelector('.video-play');
const overviewStatus = document.querySelector('#overview-status');
if (overviewVideo && overviewPlay) {
  overviewPlay.hidden = false;
  overviewVideo.addEventListener('playing', () => { overviewPlay.hidden = true; });
  overviewPlay.addEventListener('click', async () => {
    overviewPlay.hidden = true;
    overviewStatus.hidden = true;
    try {
      await overviewVideo.play();
    } catch {
      overviewStatus.textContent = 'Playback could not start. Try the player controls or download the MP4 below.';
      overviewStatus.hidden = false;
    }
  });
}
