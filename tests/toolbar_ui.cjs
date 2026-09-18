const assert = require('node:assert/strict');

async function pinnedToolbar(page, selector, moveSelector, hideSelector) {
  const toolbar = page.locator(selector), strip = toolbar.locator(':scope > .toolbar-scroll');
  await strip.evaluate(e => e.scrollLeft = 0);
  const move = page.locator(moveSelector), hide = page.locator(hideSelector);
  const beforeMove = await move.boundingBox(), beforeHide = await hide.boundingBox();
  const bounds = await toolbar.boundingBox();
  assert.ok(beforeMove.x >= bounds.x && beforeHide.x + beforeHide.width <= bounds.x + bounds.width + 1);
  assert.ok(await strip.evaluate(e => e.scrollWidth > e.clientWidth), 'Narrow toolbar must have overflowing controls');
  await hide.hover();
  await page.mouse.wheel(0, 280);
  await page.waitForFunction(selector => document.querySelector(selector + ' > .toolbar-scroll').scrollLeft > 0, selector);
  const afterMove = await move.boundingBox(), afterHide = await hide.boundingBox();
  assert.ok(Math.abs(afterMove.x - beforeMove.x) < 1, 'Move stays pinned when controls scroll');
  assert.ok(Math.abs(afterHide.x - beforeHide.x) < 1, 'Hide stays pinned when controls scroll');
  const ctrl = await strip.evaluate(e => {
    const event = new WheelEvent('wheel', {deltaY:60, ctrlKey:true, bubbles:true, cancelable:true});
    e.dispatchEvent(event); return event.defaultPrevented;
  });
  assert.equal(ctrl, false, 'Ctrl+wheel keeps its native handling');
  await page.mouse.wheel(0, -10000);
  await page.waitForFunction(selector => document.querySelector(selector + ' > .toolbar-scroll').scrollLeft === 0, selector);
}
module.exports = {pinnedToolbar};
