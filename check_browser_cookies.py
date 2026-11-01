import sqlite3
import os
import shutil
import tempfile
import sys

def check_chrome_cookies():
    cookie_path = os.path.expanduser(r'~\AppData\Local\Google\Chrome\User Data\Default\Network\Cookies')
    if not os.path.exists(cookie_path):
        print('Chrome cookies not found')
        return
    tmp = tempfile.mktemp()
    try:
        shutil.copy2(cookie_path, tmp)
        conn = sqlite3.connect(tmp)
        c = conn.cursor()
        c.execute("SELECT host_key, name FROM cookies WHERE host_key LIKE '%elevenlabs%'")
        rows = c.fetchall()
        print('Chrome ElevenLabs cookies:', len(rows))
        for r in rows:
            print('  ', r[0], r[1])
        conn.close()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def check_firefox_cookies():
    import glob
    profiles = glob.glob(os.path.expanduser(r'~\AppData\Roaming\Mozilla\Firefox\Profiles\*'))
    for prof in profiles:
        ck = os.path.join(prof, 'cookies.sqlite')
        if os.path.exists(ck):
            tmp = tempfile.mktemp()
            try:
                shutil.copy2(ck, tmp)
                conn = sqlite3.connect(tmp)
                c = conn.cursor()
                c.execute("SELECT host, name FROM moz_cookies WHERE host LIKE '%elevenlabs%'")
                rows = c.fetchall()
                print(f'Firefox ({os.path.basename(prof)}) ElevenLabs cookies:', len(rows))
                for r in rows:
                    print('  ', r[0], r[1])
                conn.close()
            except Exception as e:
                print('Firefox err:', e)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)

if __name__ == '__main__':
    check_chrome_cookies()
    check_firefox_cookies()
