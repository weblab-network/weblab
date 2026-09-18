// Open the visible disclosure before using an action; keeps browser regressions
// on the same path as users rather than invoking hidden controls directly.
async function consoleAction(locator, touch = false) {
  const menu = locator.locator('xpath=ancestor::details[1]');
  const activate = el => touch ? el.tap() : el.click();
  if (await menu.count() && !(await menu.evaluate(el => el.open))) await activate(menu.locator('summary'));
  await activate(locator);
}
module.exports = {consoleAction};
