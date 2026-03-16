import requests
import time
import yaml
import sqlite3
from datetime import datetime

CONFIG_FILE = "config.yaml"
DB_FILE = "prices.db"

class FuelWatcher:

    def __init__(self):

        with open(CONFIG_FILE) as f:
            self.config = yaml.safe_load(f)

        self.api = self.config["tankerkoenig_api"]

        self.token = self.config["telegram"]["token"]
        self.chat_id = self.config["telegram"]["chat_id"]

        self.last_update_id = None

        self.db = sqlite3.connect(DB_FILE)
        self.init_db()

    def init_db(self):

        cur = self.db.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS prices(
            time TEXT,
            location TEXT,
            fuel TEXT,
            station TEXT,
            price REAL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS heating(
            time TEXT,
            price REAL
        )
        """)

        self.db.commit()

    def telegram(self,msg):

        url=f"https://api.telegram.org/bot{self.token}/sendMessage"

        requests.post(url,data={
            "chat_id":self.chat_id,
            "text":msg
        })

    def get_updates(self):

        url=f"https://api.telegram.org/bot{self.token}/getUpdates"

        r=requests.get(url,params={"offset":self.last_update_id})

        return r.json()

    def tankerkoenig(self,lat,lon,fuel):

        url="https://creativecommons.tankerkoenig.de/json/list.php"

        params={
            "lat":lat,
            "lng":lon,
            "rad":self.config["settings"]["radius_km"],
            "sort":"price",
            "type":fuel,
            "apikey":self.api
        }

        r=requests.get(url,params=params).json()

        stations=[s for s in r["stations"] if s["isOpen"] and s["price"] is not None and s["price"]>0]

        if not stations:
            return None

        s=stations[0]

        return {
            "name":s["name"],
            "street":s["street"],
            "city":s["place"],
            "price":s["price"]
        }

    def last_price(self,location,fuel):

        cur=self.db.cursor()

        cur.execute("""
        SELECT price FROM prices
        WHERE location=? AND fuel=?
        ORDER BY time DESC LIMIT 1
        """,(location,fuel))

        r=cur.fetchone()

        if r:
            return r[0]

        return None

    def store_price(self,location,fuel,station,price):

        cur=self.db.cursor()

        cur.execute("""
        INSERT INTO prices VALUES(?,?,?,?,?)
        """,(datetime.now().isoformat(),location,fuel,station,price))

        self.db.commit()

    def check_prices(self):

        for key,loc in self.config["locations"].items():

            lat=loc["lat"]
            lon=loc["lon"]

            for fuel in loc["fuels"]:

                best=self.tankerkoenig(lat,lon,fuel)

                if not best:
                    continue

                last=self.last_price(key,fuel)

                self.store_price(key,fuel,best["name"],best["price"])

                if last and best["price"] < last - self.config["settings"]["price_drop"]:

                    msg=f"""
⛽ Preis gefallen

Ort: {loc["name"]}
Kraftstoff: {fuel}

{best["name"]}
{best["street"]} {best["city"]}

Neu: {best["price"]} €
Alt: {last} €
"""

                    self.telegram(msg)

    def heating_price(self):

        if not self.config["heating_oil"]["enabled"]:
            return

        plz=self.config["heating_oil"]["plz"]

        url=f"https://api.heizoel24.de/price?plz={plz}"

        try:
            r=requests.get(url).json()

            price=float(r["price"])

        except:
            return

        cur=self.db.cursor()

        cur.execute("SELECT price FROM heating ORDER BY time DESC LIMIT 1")

        last=cur.fetchone()

        if last:

            if price < last[0]-self.config["heating_oil"]["drop_threshold"]:

                self.telegram(
                    f"🔥 Heizölpreis gefallen\nNeu: {price} €/100L\nAlt: {last[0]}"
                )

        cur.execute("INSERT INTO heating VALUES(?,?)",(datetime.now().isoformat(),price))

        self.db.commit()

    def command(self,cmd):

        if cmd=="/help":

            self.telegram(
"""
Commands

/price
/diesel
/e10
/heizoel
/status
"""
            )

        if cmd=="/status":

            self.telegram("Fuelwatch läuft.")

    def check_commands(self):

        data=self.get_updates()

        if not data["result"]:
            return

        for u in data["result"]:

            self.last_update_id=u["update_id"]+1

            try:
                cmd=u["message"]["text"]

                self.command(cmd)

            except:
                pass

    def run(self):

        self.telegram("Fuelwatch gestartet")

        while True:

            try:

                self.check_prices()
                self.heating_price()
                self.check_commands()

            except Exception as e:

                self.telegram(f"Fehler: {e}")

            time.sleep(self.config["settings"]["check_interval"])


if __name__=="__main__":

    fw=FuelWatcher()
    fw.run()
