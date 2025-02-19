from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import json
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from fake_useragent import UserAgent
import time
import re
from functools import lru_cache
import threading
import logging

# Configuration des logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
load_dotenv()

# Cache des résultats
search_cache = {}
cache_lock = threading.Lock()
CACHE_DURATION = 3600  # 1 heure en secondes

class HotelSearchProvider:
    def search(self, query):
        raise NotImplementedError("Les fournisseurs doivent implémenter la méthode search")

class BookingScraperProvider(HotelSearchProvider):
    def __init__(self):
        logger.info("Initialisation du BookingScraperProvider")
        self.ua = UserAgent()
        firefox_options = FirefoxOptions()
        firefox_options.add_argument('--headless')
        firefox_options.add_argument('--disable-blink-features=AutomationControlled')
        firefox_options.add_argument('--disable-notifications')
        firefox_options.add_argument('--lang=fr-FR')
        firefox_options.add_argument('--no-sandbox')
        firefox_options.add_argument('--disable-dev-shm-usage')
        firefox_options.add_argument('--disable-gpu')
        firefox_options.add_argument('--remote-debugging-port=9222')
        firefox_options.add_argument('--disable-extensions')
        firefox_options.add_argument('--disable-sync')
        firefox_options.add_argument('--disable-default-apps')
        firefox_options.set_preference('general.useragent.override', self.ua.random)
        firefox_options.set_preference('dom.webdriver.enabled', False)
        firefox_options.set_preference('useAutomationExtension', False)
        firefox_options.set_preference('network.http.connection-timeout', 10)
        firefox_options.set_preference('network.http.response-timeout', 10)
        
        self.driver = None
        self.options = firefox_options
        logger.info("BookingScraperProvider initialisé avec succès")

    def get_driver(self):
        if self.driver is None:
            try:
                logger.info("Création d'une nouvelle instance du driver Firefox")
                start_time = time.time()
                self.driver = webdriver.Firefox(options=self.options)
                self.driver.set_page_load_timeout(10)
                self.driver.set_script_timeout(10)
                self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                end_time = time.time()
                logger.info(f"Driver Firefox créé avec succès en {end_time - start_time:.2f} secondes")
            except Exception as e:
                logger.error(f"Erreur lors de la création du driver Firefox: {str(e)}")
                if self.driver:
                    try:
                        self.driver.quit()
                    except:
                        pass
                    self.driver = None
                raise e
        return self.driver

    def extract_rating(self, text):
        try:
            match = re.search(r'(\d+[,.]\d+)', text)
            if match:
                rating_str = match.group(1).replace(',', '.')
                rating = float(rating_str) / 2
                logger.debug(f"Note extraite : {rating}/5 (original: {text})")
                return rating
            return 0
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction de la note : {str(e)}")
            return 0

    def search(self, query):
        logger.info(f"Début de la recherche pour : {query}")
        cache_key = f"booking_{query}"
        current_time = time.time()
        
        with cache_lock:
            if cache_key in search_cache:
                cached_result = search_cache[cache_key]
                if current_time - cached_result['timestamp'] < CACHE_DURATION:
                    logger.info(f"Résultats trouvés dans le cache pour : {query}")
                    return cached_result['data']
        
        start_time = time.time()
        try:
            driver = self.get_driver()
            search_query = query.replace(' ', '+')
            current_date = datetime.now()
            check_in = (current_date + timedelta(days=1)).strftime("%Y-%m-%d")
            check_out = (current_date + timedelta(days=2)).strftime("%Y-%m-%d")
            
            url = f"https://www.booking.com/searchresults.fr.html?ss={search_query}&checkin={check_in}&checkout={check_out}&lang=fr&selected_currency=EUR"
            logger.info(f"Accès à l'URL : {url}")
            
            driver.get(url)
            time.sleep(1)  # Réduit à 1 seconde
            logger.info("Page chargée, attente des éléments...")

            wait = WebDriverWait(driver, 5)  # Réduit à 5 secondes
            try:
                hotel_cards = wait.until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div[data-testid="property-card"]'))
                )
                logger.info(f"Nombre total de cartes d'hôtels trouvées : {len(hotel_cards)}")
            except Exception as e:
                logger.error(f"Aucun hôtel trouvé : {str(e)}")
                return []
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            hotel_cards = soup.find_all('div', {'data-testid': 'property-card'})
            
            results = []
            for index, card in enumerate(hotel_cards[:2], 1):  # Réduit à 2 hôtels
                logger.info(f"Traitement de l'hôtel {index}/2")
                try:
                    title_element = card.find('div', {'data-testid': 'title'})
                    if not title_element:
                        logger.warning(f"Pas de titre trouvé pour l'hôtel {index}")
                        continue
                        
                    name = title_element.text.strip()
                    if not name:
                        logger.warning(f"Nom vide pour l'hôtel {index}")
                        continue

                    logger.info(f"Extraction des données pour l'hôtel : {name}")
                    hotel_data = {
                        "name": name,
                        "rating": 0,
                        "location": "Adresse non disponible",
                        "price": "Prix non disponible",
                        "image": "",
                        "booking_url": "",
                        "source": "Booking.com"
                    }

                    # Extraction rapide des données
                    if address_element := card.find('span', {'data-testid': 'address'}):
                        hotel_data["location"] = address_element.text.strip()
                    
                    if price_element := (card.find('span', {'data-testid': 'price-and-discounted-price'}) or card.find('span', {'data-testid': 'price'})):
                        hotel_data["price"] = price_element.text.strip()
                    
                    if score_element := card.find('div', {'data-testid': 'review-score'}):
                        hotel_data["rating"] = self.extract_rating(score_element.text.strip())
                    
                    if img_element := card.find('img', {'data-testid': 'image'}):
                        if 'src' in img_element.attrs:
                            hotel_data["image"] = img_element['src']
                    
                    if link_element := (card.find('a', {'data-testid': 'title-link'}) or card.find('a', class_='e13098a59f')):
                        if 'href' in link_element.attrs:
                            href = link_element['href']
                            if href.startswith('//'):
                                href = 'https:' + href
                            elif href.startswith('/'):
                                href = 'https://www.booking.com' + href
                            hotel_data["booking_url"] = href

                    results.append(hotel_data)
                    logger.info(f"Hôtel {index} traité avec succès")
                except Exception as e:
                    logger.error(f"Erreur lors du parsing de l'hôtel {index}: {str(e)}")
                    continue

            end_time = time.time()
            logger.info(f"Recherche terminée en {end_time - start_time:.2f} secondes, {len(results)} hôtels trouvés")
            
            with cache_lock:
                search_cache[cache_key] = {
                    'timestamp': current_time,
                    'data': results
                }
            
            return results
        except Exception as e:
            logger.error(f"Erreur générale du Booking Scraper: {str(e)}")
            return []
        finally:
            if self.driver:
                try:
                    self.driver.quit()
                    logger.info("Driver Firefox fermé avec succès")
                except:
                    logger.error("Erreur lors de la fermeture du driver Firefox")
                    pass
                self.driver = None

# Initialisation des fournisseurs
providers = [
    BookingScraperProvider()
]

@app.route('/')
def home():
    logger.info("Accès à la page d'accueil")
    return render_template('index.html')

@app.route('/api/search', methods=['POST'])
def search_hotels():
    query = request.json.get('query', '')
    logger.info(f"Nouvelle recherche reçue : {query}")
    
    if not query:
        logger.warning("Recherche vide reçue")
        return jsonify({"results": [], "error": "Veuillez entrer un terme de recherche"})
    
    all_results = []
    
    for provider in providers:
        try:
            logger.info(f"Recherche via le fournisseur : {provider.__class__.__name__}")
            results = provider.search(query)
            all_results.extend(results)
        except Exception as e:
            logger.error(f"Erreur avec le fournisseur {provider.__class__.__name__}: {str(e)}")
    
    all_results.sort(key=lambda x: float(x['rating'] if x['rating'] else 0), reverse=True)
    logger.info(f"Résultats triés, {len(all_results)} hôtels au total")
    
    if not all_results:
        logger.warning("Aucun résultat trouvé")
        all_results = [{
            "name": "Aucun résultat trouvé",
            "rating": 0,
            "location": "Veuillez essayer une autre recherche",
            "price": "N/A",
            "image": "",
            "booking_url": "",
            "source": "Système"
        }]
    
    logger.info("Envoi des résultats au client")
    return jsonify({"results": all_results})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Démarrage du serveur sur le port {port}")
    app.run(host='0.0.0.0', port=port) 