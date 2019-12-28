import pandas as pd
from fastbt.Meta import Broker

from kiteconnect import KiteConnect
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from kiteconnect.exceptions import (
    TokenException,
    NetworkException,
    GeneralException,
    KiteException
)

def get_key(url, key='request_token'):
    """
    Get the required key from the query parameter
    """
    from urllib.parse import parse_qs, urlparse
    req = urlparse(url)
    key = parse_qs(req.query).get(key)
    if key is None:
        return None
    else:
        return key[0]


class Zerodha(Broker):
    """
    Automated Trading class
    """
    def __init__(self, api_key, secret, user_id, password,
                PIN, exchange='NSE',
                product='MIS'):
        self._api_key = api_key
        self._secret = secret
        self._user_id = user_id
        self._password = password
        self._pin = PIN
        self.exchange = exchange
        self.product = product
        self._store_access_token = True        
        super(Zerodha, self).__init__()

    @property
    def isNilPositions(self):
        """
        return True if there are no open positions
        else return False
        """
        temp = pd.DataFrame(self.positions()['net'])
        if temp.quantity.abs().sum() == 0:
            return True
        else:
            return False

    @property
    def isNilPositionsDay(self):
        """
        return True if there are no open positions for 
        the day else return False
        """
        temp = pd.DataFrame(self.positions()['day'])
        if temp.quantity.abs().sum() == 0:
            return True
        else:
            return False    

    @property
    def isNilOrders(self):
        """
        return True if there are no pending orders 
        else return False
        """   
        orders = pd.DataFrame(self.orders())
        pending = orders.pending_quantity.abs().sum()
        canceled = orders.cancelled_quantity.abs().sum()
        net_pending = pending - canceled
        if net_pending == 0:
            return True
        else:
            return False

    def cancel_all_orders(self):
        """
        Cancel all existing orders
        """
        for o in self.orders():
            self.kite.cancel_order(variety='regular', order_id=o['order_id'])
        i = 0
        while not(self.isNilOrders):
            print('Into the loop')
            i+=1
            for o in self.orders():
                self.kite.cancel_order(variety='regular', order_id=o['order_id'])
            if i > 5:
                print('Breaking out of loop without canceling all orders')
                break

    def _shortcuts(self):
        """
        Provides shortcuts to kite functions by mapping functions.
        Instead of calling at.kite.quote, you would directly call
        at.quote.
        Note
        -----
        1) Kite functions are initialized only after authentication
        1) Not all functions are supported
        """
        self.margins = self.kite.margins
        self.profile = self.kite.profile
        self.ltp = self.kite.ltp
        self.quote = self.kite.quote
        self.ohlc = self.kite.ohlc
        self.positions = self.kite.positions
        self.trades = self.kite.trades
        self.orders = self.kite.orders
        self.holdings = self.kite.holdings
        self._sides = {'BUY': 'SELL', 'SELL': 'BUY'}


    def authenticate(self):
        """
        Authenticates a kite session if access token is already available
        Looks up token in token.tok file
        Useful for reconnecting instead of logging in again
        """
        try:
            self.kite = KiteConnect(api_key=self._api_key)
            with open('token.tok') as f:
                access_token = f.read()
            self.kite.set_access_token(access_token)
            self.kite.profile()
            self._shortcuts()
        except TokenException:
            print('Into Exception')
            self._login()
            self._shortcuts()
        except:
            print('Unknown Exception')
            self._login()
            self._shortcuts()        
        
    def _login(self):
        import time
        self.kite = KiteConnect(api_key=self._api_key)
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')
        driver = webdriver.Chrome(options=options)
        driver.get(self.kite.login_url())
        login_form = WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CLASS_NAME, "login-form")))
        login_form.find_elements_by_tag_name('input')[0].send_keys(self._user_id)
        login_form.find_elements_by_tag_name('input')[1].send_keys(self._password)
        WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CLASS_NAME, "button-orange")))
        driver.find_element_by_xpath('//button[@type="submit"]').click()
        twofa_form = WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CLASS_NAME, "twofa-form")))
        twofa_form.find_elements_by_tag_name('input')[0].send_keys(self._pin)
        WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CLASS_NAME, "button-orange")))
        driver.find_element_by_xpath('//button[@type="submit"]').click() 
        time.sleep(2)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "email")))
        token = get_key(driver.current_url)
        access = self.kite.generate_session(request_token=token, api_secret=self._secret)
        self.kite.set_access_token(access['access_token'])
        with open("token.tok", "w") as f:
            f.write(access['access_token'])
        driver.close()

    def get_all_orders_and_positions(self, positions='day'):
        """
        Get the summary of all orders and positions
        """
        pos = pd.DataFrame(self.positions()[positions])
        orders = pd.DataFrame(self.orders())
        orders['qty'] = orders.eval('pending_quantity-cancelled_quantity')
        orders['typ'] = 'orders'
        pos['qty'] = pos['quantity'].abs()
        pos['transaction_type'] = ['SELL' if qty < 0 else 'BUY' for 
                           qty in pos.quantity]
        pos['typ'] = 'positions'
        cols = ['tradingsymbol', 'transaction_type', 'qty', 'typ']
        return pd.concat([pos,orders], sort=False)[cols]

    def uncovered(self):
        """
        Return the list of uncovered positions
        A position is considered unconvered if there is no matching
        stop loss or target order.
        """
        pass

    def get_order_type(self, price, ltp, order):
        if order == "BUY":
            return 'LIMIT' if price < ltp else 'SL'
        elif order == "SELL":
            return 'LIMIT' if price > ltp else 'SL'
 
    def _custom_orders(self, data, **kwargs):
        """
        Generate custom orders.
        This is for customized usage
        data
            dataframe with the following columns
            symbol, price, side, quantity and stop_loss
        kwargs
            keyword arguments to be included in each order
        """
        cols = ['symbol', 'price', 'quantity', 'side', 'stop_loss']
        data = data[cols].to_dict(orient='records')
        exchange = kwargs.get('exchange', 'NSE')
        sym = ['{e}:{s}'.format(e=exchange, s=x['symbol']) for x in data]
        ltps = self.ltp(sym)
        ltps = {k[4:]:v['last_price'] for k,v in ltps.items()}
        print(ltps)
        all_orders = []
        replace = {
            'symbol': 'tradingsymbol',
            'side': 'transaction_type',            
            }
        for d in data:
            dct = d.copy()
            del dct['stop_loss']
            ltp = ltps.get(d['symbol'])
            order_type = self.get_order_type(price=d['price'],
                ltp=ltp, order=d['side'])
            dct['order_type'] = order_type
            if order_type == "SL":
                dct['trigger_price'] = dct['price'] - 0.05
            dct.update(kwargs)
            all_orders.append(self.rename(dct, keys=replace))
        # Second leg for cover orders
        for d in data:
            dct = d.copy()
            del dct['price']
            dct['side'] = self._sides[dct['side']]
            dct['order_type'] = 'SL-M'
            dct.update(kwargs)
            replace.update({'stop_loss': 'price'})
            all_orders.append(self.rename(dct, keys=replace))
        return all_orders

