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
async function scrollingTabs(page, selector) {
  const strip=page.locator(selector);
  await strip.evaluate(e=>e.scrollLeft=0);
  assert.ok(await strip.evaluate(e=>e.scrollWidth>e.clientWidth),'Tab strip must overflow');
  const selected=await strip.locator('[role=tab][aria-selected=true]').getAttribute('id');
  await strip.hover();await page.mouse.wheel(0,180);
  await page.waitForFunction(s=>document.querySelector(s).scrollLeft>0,selector);
  assert.equal(await strip.locator('[role=tab][aria-selected=true]').getAttribute('id'),selected);
  for (const modifiers of [{ctrlKey:true},{metaKey:true},{deltaX:200}]) {
    const prevented=await strip.evaluate((e,modifiers)=>{
      const event=new WheelEvent('wheel',{deltaY:50,bubbles:true,cancelable:true,...modifiers});
      e.dispatchEvent(event);return event.defaultPrevented;
    },modifiers);
    assert.equal(prevented,false,'Keep zoom and horizontal trackpad handling native');
  }
  await page.mouse.wheel(0,-10000);
  await page.waitForFunction(s=>document.querySelector(s).scrollLeft===0,selector);
}
module.exports = {pinnedToolbar,scrollingTabs};
