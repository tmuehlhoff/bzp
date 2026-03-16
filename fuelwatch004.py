import requests
import time
import yaml
import sqlite3
from datetime import datetime
import matplotlib.pyplot as plt
import io

CONFIG_FILE="config.yaml"
DB_FILE="prices.db"


class FuelWatcher:

    def __init__(self):

        with open(CONFIG_FILE) as f:
            self.config=yaml.safe_load(f)

        self.api=self.config["tankerkoenig_api"]

        self.token=self.config["telegram"]["token"]
        self.chat_id=self.config["telegram"]["chat_id"]

        self.last_update_id=None
        self.last_error=None

        self.db=sqlite3.connect(DB_FILE)

        self.init_db()

    def init_db(self):

        cur=self.db.cursor()

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

    def now(self):
        return datetime.now().isoformat()

    # ---------------- Telegram ----------------

    def telegram(self,msg):

        url=f"https://api.telegram.org/bot{self.token}/sendMessage"

        requests.post(url,data={
            "chat_id":self.chat_id,
            "text":msg
        })

    def telegram_photo(self,img):

        url=f"https://api.telegram.org/bot{self.token}/sendPhoto"

        requests.post(
            url,
            data={"chat_id":self.chat_id},
            files={"photo":img}
        )


# ------------------------------------------------
# Tankerkönig
# ------------------------------------------------

    def station_list(self,lat,lon):

        url="https://creativecommons.tankerkoenig.de/json/list.php"

        params={
            "lat":lat,
            "lng":lon,
            "rad":self.config["settings"]["radius_km"],
            "sort":"price",
            "type":"all",
            "apikey":self.api
        }

        r=requests.get(url,params=params,timeout=10).json()

        stations=[]

        for s in r.get("stations",[]):

            if not s.get("isOpen"):
                continue

            name=s.get("name","")

            hvo=False
            if "hvo" in name.lower():
                hvo=True

            stations.append({
                "name":name,
                "street":s.get("street"),
                "city":s.get("place"),
                "diesel":s.get("diesel"),
                "e10":s.get("e10"),
                "hvo":hvo
            })

        return stations


    def cheapest(self,stations,fuel):

        cheapest=None
        best=999

        for s in stations:

            price=s.get(fuel)

            if price is None:
                continue

            if price < best:

                best=price
                cheapest=s

        if cheapest:
            cheapest["price"]=best

        return cheapest


# ------------------------------------------------
# Datenbank
# ------------------------------------------------

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
        """,(self.now(),location,fuel,station,price))

        self.db.commit()


# ------------------------------------------------
# Preisprüfung
# ------------------------------------------------

    def check_prices(self,startup=False):

        summary=[]

        for key,loc in self.config["locations"].items():

            lat=loc["lat"]
            lon=loc["lon"]

            stations=self.station_list(lat,lon)

            for fuel in loc["fuels"]:

                best=self.cheapest(stations,fuel)

                if not best:
                    continue

                last=self.last_price(key,fuel)

                self.store_price(key,fuel,best["name"],best["price"])

                if startup:

                    summary.append(
                        f"{loc['name']} {fuel.upper()} {best['price']:.3f} €"
                    )
                    continue

                if last is None:
                    continue

                if best["price"] < last-self.config["settings"]["price_drop"]:

                    msg=f"""
⛽ Preis gefallen

Ort: {loc["name"]}
Kraftstoff: {fuel}

{best["name"]}
{best["street"]} {best["city"]}

Neu: {best["price"]:.3f} €
Alt: {last:.3f} €
"""

                    self.telegram(msg)

        if startup and summary:

            msg="⛽ Fuelwatch gestartet\n\n"

            for s in summary:
                msg+=s+"\n"

            self.telegram(msg)


# ------------------------------------------------
# Top Tankstellen
# ------------------------------------------------

    def top_stations(self,fuel):

        results=[]

        for key,loc in self.config["locations"].items():

            stations=self.station_list(loc["lat"],loc["lon"])

            for s in stations:

                price=s.get(fuel)

                if price:

                    results.append((price,s))

        results.sort(key=lambda x:x[0])

        msg=f"⛽ Top Tankstellen {fuel}\n\n"

        for price,s in results[:10]:

            msg+=f"{price:.3f} € {s['name']} {s['city']}\n"

        self.telegram(msg)


# ------------------------------------------------
# Chart
# ------------------------------------------------

    def chart(self,fuel):

        cur=self.db.cursor()

        cur.execute("""
        SELECT time,price FROM prices
        WHERE fuel=?
        ORDER BY time
        """,(fuel,))

        rows=cur.fetchall()

        if not rows:
            self.telegram("Keine Daten vorhanden")
            return

        times=[r[0] for r in rows]
        prices=[r[1] for r in rows]

        plt.figure()
        plt.plot(prices)

        plt.title(f"Preisverlauf {fuel}")
        plt.ylabel("€")
        plt.xlabel("Messpunkte")

        buf=io.BytesIO()
        plt.savefig(buf,format="png")
        buf.seek(0)

        self.telegram_photo(buf)

        plt.close()


# ------------------------------------------------
# Telegram Commands
# ------------------------------------------------

    def get_updates(self):

        url=f"https://api.telegram.org/bot{self.token}/getUpdates"

        r=requests.get(url,params={"offset":self.last_update_id},timeout=10)

        return r.json()


    def command(self,cmd):

        if cmd.startswith("/chart"):

            fuel=cmd.split(" ")[1]

            self.chart(fuel)

        elif cmd.startswith("/top"):

            fuel=cmd.split(" ")[1]

            self.top_stations(fuel)

        elif cmd=="/status":

            self.telegram("Fuelwatch läuft")

        elif cmd=="/price":

            self.check_prices()
            self.telegram("Preise geprüft")

        elif cmd=="/help":

            self.telegram("""
Commands

/chart diesel
/chart e10

/top diesel
/top e10

/price
/status
""")


    def check_commands(self):

        data=self.get_updates()

        for u in data.get("result",[]):

            self.last_update_id=u["update_id"]+1

            try:

                cmd=u["message"]["text"]

                self.command(cmd)

            except:
                pass


# ------------------------------------------------
# MAIN
# ------------------------------------------------

    def run(self):

        self.check_prices(startup=True)

        while True:

            try:

                self.check_prices()
                self.check_commands()

                self.last_error=None

            except Exception as e:

                err=str(e)

                if err!=self.last_error:

                    self.telegram(f"Fehler: {err}")

                    self.last_error=err

            time.sleep(self.config["settings"]["check_interval"])


if __name__=="__main__":

    fw=FuelWatcher()
    fw.run()
