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

app = Flask(__name__)
CORS(app)
load_dotenv()

class HotelSearchProvider:
    def search(self, query):
        raise NotImplementedError("Les fournisseurs doivent implémenter la méthode search")

class BookingScraperProvider(HotelSearchProvider):
    def __init__(self):
        self.ua = UserAgent()
        firefox_options = FirefoxOptions()
        firefox_options.add_argument('--headless')
        firefox_options.add_argument('--disable-blink-features=AutomationControlled')
        firefox_options.add_argument('--disable-notifications')
        firefox_options.add_argument('--lang=fr-FR')
        firefox_options.set_preference('general.useragent.override', self.ua.random)
        firefox_options.set_preference('dom.webdriver.enabled', False)
        firefox_options.set_preference('useAutomationExtension', False)
        
        self.driver = webdriver.Firefox(options=firefox_options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def extract_rating(self, text):
        try:
            # Extraction du premier nombre avec virgule (ex: "8,9" de "Avec une note de 8,98,9")
            match = re.search(r'(\d+[,.]\d+)', text)
            if match:
                # Remplacer la virgule par un point pour la conversion en float
                rating_str = match.group(1).replace(',', '.')
                # Convertir la note sur 10 en note sur 5
                return float(rating_str) / 2
            return 0
        except Exception:
            return 0

    def search(self, query):
        try:
            # Formatage de la requête pour l'URL
            search_query = query.replace(' ', '+')
            current_date = datetime.now()
            check_in = (current_date + timedelta(days=1)).strftime("%Y-%m-%d")
            check_out = (current_date + timedelta(days=2)).strftime("%Y-%m-%d")
            
            url = f"https://www.booking.com/searchresults.fr.html?ss={search_query}&checkin={check_in}&checkout={check_out}&lang=fr&selected_currency=EUR"
            
            self.driver.get(url)
            time.sleep(5)  # Attente plus longue pour le chargement

            # Attente explicite pour les cartes d'hôtels
            wait = WebDriverWait(self.driver, 10)
            try:
                hotel_cards = wait.until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div[data-testid="property-card"]'))
                )
            except Exception as e:
                print(f"Aucun hôtel trouvé: {str(e)}")
                return []
            
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            hotel_cards = soup.find_all('div', {'data-testid': 'property-card'})
            
            results = []
            for card in hotel_cards[:10]:
                try:
                    # Extraction du nom avec fallback
                    title_element = card.find('div', {'data-testid': 'title'})
                    name = title_element.text.strip() if title_element else "Nom non disponible"

                    # Extraction de l'adresse avec fallback
                    address_element = card.find('span', {'data-testid': 'address'})
                    location = address_element.text.strip() if address_element else "Adresse non disponible"
                    
                    # Extraction du prix avec fallback
                    price = "Prix non disponible"
                    price_element = card.find('span', {'data-testid': 'price-and-discounted-price'})
                    if not price_element:
                        price_element = card.find('span', {'data-testid': 'price'})
                    if price_element:
                        price = price_element.text.strip()
                    
                    # Extraction de la note avec gestion d'erreur améliorée
                    rating = 0
                    score_element = card.find('div', {'data-testid': 'review-score'})
                    if score_element:
                        score_text = score_element.text.strip()
                        rating = self.extract_rating(score_text)
                    
                    # Extraction de l'image avec fallback
                    image_url = ''
                    img_element = card.find('img', {'data-testid': 'image'})
                    if img_element and 'src' in img_element.attrs:
                        image_url = img_element['src']
                    
                    # Extraction du lien avec fallback
                    booking_url = ''
                    link_element = card.find('a', {'data-testid': 'title-link'})
                    if not link_element:
                        link_element = card.find('a', class_='e13098a59f')  # Classe alternative
                    
                    if link_element and 'href' in link_element.attrs:
                        href = link_element['href']
                        if href.startswith('//'):
                            href = 'https:' + href
                        elif href.startswith('/'):
                            href = 'https://www.booking.com' + href
                        booking_url = href

                    # Ne créer l'objet hotel que si nous avons au moins le nom
                    if name != "Nom non disponible":
                        hotel_data = {
                            "name": name,
                            "rating": rating,
                            "location": location,
                            "price": price,
                            "image": image_url,
                            "booking_url": booking_url,
                            "source": "Booking.com"
                        }
                        results.append(hotel_data)
                except Exception as e:
                    print(f"Erreur lors du parsing d'un hôtel: {str(e)}")
                    continue
            
            return results
        except Exception as e:
            print(f"Erreur Booking Scraper: {str(e)}")
            return []
        finally:
            self.driver.quit()

# Initialisation des fournisseurs
providers = [
    BookingScraperProvider()
]

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/search', methods=['POST'])
def search_hotels():
    query = request.json.get('query', '')
    if not query:
        return jsonify({"results": [], "error": "Veuillez entrer un terme de recherche"})
    
    all_results = []
    
    # Recherche via tous les fournisseurs
    for provider in providers:
        try:
            results = provider.search(query)
            all_results.extend(results)
        except Exception as e:
            print(f"Erreur avec le fournisseur {provider.__class__.__name__}: {str(e)}")
    
    # Tri des résultats par note (du plus haut au plus bas)
    all_results.sort(key=lambda x: float(x['rating'] if x['rating'] else 0), reverse=True)
    
    if not all_results:
        all_results = [{
            "name": "Aucun résultat trouvé",
            "rating": 0,
            "location": "Veuillez essayer une autre recherche",
            "price": "N/A",
            "image": "",
            "booking_url": "",
            "source": "Système"
        }]
    
    return jsonify({"results": all_results})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port) 