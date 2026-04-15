import asyncio, sys
sys.path.insert(0, "/home/zamoritacr/joao-spine")
from services.llm_router import complete, summarize, classify, generate_code, reason, council_task, stream_complete, health_check

async def main():
    results = {}
    print("=" * 50)
    print("JOAO LLMRouter Tests - Ollama Local")
    print("=" * 50)

    print("\n[1] health check...")
    h = await health_check()
    results["health"] = h["status"] == "ok"
    print(f"    {h}")

    print("\n[2] completion...")
    try:
        r = await complete([{"role":"user","content":"What is 2+2? Reply with just the number."}], task_type="fallback", max_tokens=10)
        results["complete"] = "4" in r
        print(f"    {r.strip()} | pass={results['complete']}")
    except Exception as e:
        results["complete"] = False
        print(f"    FAIL: {e}")

    print("\n[3] summarize...")
    try:
        r = await summarize("JOAO is an AI exocortex built by Johan Zamora at The Art of The Possible. It has 16 Council agents and runs on a ROG Strix server in Denver.")
        results["summarize"] = len(r) > 10
        print(f"    {r[:100]} | pass={results['summarize']}")
    except Exception as e:
        results["summarize"] = False
        print(f"    FAIL: {e}")

    print("\n[4] classify...")
    try:
        r = await classify("Write a Python function", ["code_generation","summarization","chat"])
        results["classify"] = "code" in r.lower()
        print(f"    {r.strip()} | pass={results['classify']}")
    except Exception as e:
        results["classify"] = False
        print(f"    FAIL: {e}")

    print("\n[5] code gen...")
    try:
        r = await generate_code("Python function: returns True if string is palindrome")
        results["codegen"] = "def " in r
        print(f"    {len(r)} chars | has def={('def ' in r)} | pass={results['codegen']}")
    except Exception as e:
        results["codegen"] = False
        print(f"    FAIL: {e}")

    print("\n[6] reason...")
    try:
        r = await reason("What are 3 risks of running a service on a 98% full disk?")
        results["reason"] = len(r) > 30
        print(f"    {r[:120]} | pass={results['reason']}")
    except Exception as e:
        results["reason"] = False
        print(f"    FAIL: {e}")

    print("\n[7] council task...")
    try:
        r = await council_task("BYTE", "List 3 actions to fix a memory leak in a Python FastAPI app.")
        results["council"] = len(r) > 20
        print(f"    {r[:120]} | pass={results['council']}")
    except Exception as e:
        results["council"] = False
        print(f"    FAIL: {e}")

    print("\n[8] streaming...")
    try:
        chunks = []
        async for chunk in stream_complete([{"role":"user","content":"Count 1 to 3, one per line."}], task_type="chat", max_tokens=30):
            chunks.append(chunk)
        results["stream"] = len(chunks) > 1
        print(f"    {len(chunks)} chunks | pass={results['stream']}")
    except Exception as e:
        results["stream"] = False
        print(f"    FAIL: {e}")

    print("\n" + "=" * 50)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)

asyncio.run(main())
