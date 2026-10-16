"""LLM-style 'code mode': model-written JS orchestrates several host tools in one go."""

import asyncio

from jsbox import JsTimeoutError, Runtime

PRICES = {"apple": 1.2, "pear": 2.5, "plum": 3.1}


async def get_price(item):
    await asyncio.sleep(0.01)  # pretend network
    return PRICES[item]


logs = []
rt = Runtime(on_console=lambda level, args: logs.append((level, args)), lossy=True)
rt.bind_function("getPrice", get_price)

# This is what a model would emit instead of N separate tool-call round trips.
model_code = """(async () => {
  const items = ['apple', 'pear', 'plum'];
  const prices = await Promise.all(items.map(i => getPrice(i)));
  console.log('fetched', prices);
  return { total: prices.reduce((a, b) => a + b, 0), cheapest: items[prices.indexOf(Math.min(...prices))] };
})()"""


async def main():
    print("result:", await rt.eval_async(model_code, timeout=5))
    print("console:", logs)
    try:
        await rt.eval_async("(async () => { while (true) {} })()", timeout=0.5)
    except JsTimeoutError:
        print("runaway code stopped")


asyncio.run(main())
