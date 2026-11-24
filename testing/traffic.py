"""One of four Locust processes. Stdout carries measurements, stderr the live trace."""
from gevent import monkey
monkey.patch_all()

import json
import math
import os
import sys
import time
from datetime import datetime

import gevent
from locust import FastHttpUser
from locust.env import Environment


class Rider(FastHttpUser):
    host = os.environ["RENTAL_URL"]
    connection_timeout = 3
    network_timeout = 10
    max_retries = 0


def cost(seconds, offer):
    minutes = max(0, seconds)  # One real second is one billable minute in the demo.
    if minutes <= offer["free_period_min"]:
        return 0
    return math.ceil(minutes / 60) * offer["price_per_hour"]


def ride(env, user_id, duration, trace):
    user = Rider(env)

    def request(method, path, name, codes, **kwargs):
        response = user.client.request(method, path, name=name, headers={"X-User-Id": user_id}, **kwargs)
        if response.status_code not in codes:
            raise RuntimeError(f"{name}: HTTP {response.status_code}, {str(response.text)[:120]}")
        return response.status_code, response.json()

    try:
        for _ in range(3):
            _, offer = request("POST", "/api/v1/offers", "offer", {201}, json={"station_id": "st-1"})

        def start():
            return request("POST", "/api/v1/orders", "start", {201, 202}, json={"offer_id": offer["offer_id"]})

        before_start = time.monotonic()
        if trace:
            print(f"Оффер {offer['offer_id']} · {offer['price_per_hour']} ₽/ч, "
                  f"первые {offer['free_period_min']} минут бесплатно, депозит {offer['deposit']} ₽", file=sys.stderr)
            attempts = [gevent.spawn(start) for _ in range(2)]
            results = [g.get() for g in attempts]
        else:
            results = [start()]
        while all(code == 202 for code, _ in results):
            if time.monotonic() - before_start > 15:
                raise RuntimeError("Заказ остался в обработке")
            gevent.sleep(1)
            results.append(start())
        order = next(body for code, body in results if code == 201)
        if any(body["order_id"] != order["order_id"] for _, body in results):
            raise ValueError("Повтор создал второй заказ")
        if trace:
            print(f"Два запроса старта · HTTP {results[0][0]}, {results[1][0]} · "
                  f"один заказ {order['order_id']}, банка {order['powerbank_id']}", file=sys.stderr)

        began = time.monotonic()
        for view in range(1, 101):
            gevent.sleep(max(0, began + duration * view / 100 - time.monotonic()))
            _, info = request("GET", "/api/v1/orders/" + order["order_id"], "info", {200})
            elapsed = (datetime.fromisoformat(info["calculated_at"]) - datetime.fromisoformat(order["start_time"])).total_seconds()
            if (info["order_id"] != order["order_id"] or info["status"] != "ACTIVE"
                    or info["amount_is_final"] or info["amount"] != cost(elapsed, offer)):
                raise ValueError("Неверное состояние или стоимость активной аренды")
            if trace and view in (1, 100):
                print(f"Просмотр {view}/100 · {info['status']} / {info['payment_state']} · {info['amount']} ₽",
                      file=sys.stderr)
        _, page = request("GET", "/api/v1/orders", "list", {200})
        if [row["order_id"] for row in page["items"]] != [order["order_id"]]:
            raise ValueError("Неверный список аренд пользователя")

        before_return = time.monotonic()
        _, returned = request("POST", os.environ["STUBS_URL"] + "/stations/return", "return", {200},
                              json={"powerbank_id": order["powerbank_id"], "station_id": "st-2"})
        total = returned["total_amount"]
        low = cost(before_return - began - 1, offer)
        high = cost(time.monotonic() - before_start + 1, offer)
        if returned["order_id"] != order["order_id"] or not low <= total <= high or total % offer["price_per_hour"]:
            raise ValueError(f"Некорректный возврат: {total} ₽, ожидалось {low}–{high} ₽")
        if trace:
            print(f"Станция приняла {order['powerbank_id']} · к оплате {total} ₽", file=sys.stderr)
    finally:
        user.client.client.clientpool.close()


if __name__ == "__main__":
    env = Environment()
    samples = []

    @env.events.request.add_listener
    def measure(name, response_time, **kwargs):
        samples.append((time.time(), name, round(response_time)))

    prefix, traced_user, *indices = sys.argv[1:]
    print("ready", flush=True)
    start_at = float(input())
    riders = []
    for index in map(int, indices):
        gevent.sleep(max(0, start_at + index - time.time()))
        user_id = f"{prefix}{index}"
        riders.append(gevent.spawn(ride, env, user_id, 2 if index % 3 == 0 else 6, user_id == traced_user))
    gevent.joinall(riders, raise_error=True)
    print(json.dumps(samples))
