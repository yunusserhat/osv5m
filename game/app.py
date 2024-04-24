"""Requires gradio==3.44.0"""
import io
import shutil 
import os
import uuid
import time
import numpy as np

from PIL import Image
from math import radians, sin, cos, sqrt, asin, exp
from os.path import join
from collections import defaultdict

import matplotlib.style as mplstyle
mplstyle.use(['fast'])
import pandas as pd

import gradio as gr
import wandb
import reverse_geocoder as rg
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt

from gradio_folium import Folium
from folium import Map, Element, LatLngPopup
from matplotlib.offsetbox import AnchoredText


IMAGE_FOLDER = './images'
CSV_FILE = './select.csv'
RESULTS_DIR = './results'
RULES = """<h1>OSV-5M (plonk)</h1>
<center><img width="256" alt="Rotating globe" src="https://upload.wikimedia.org/wikipedia/commons/6/6b/Rotating_globe.gif"></center>
<h2> Instructions </h2>
<h3> Click on the map 🗺️ (left) to the location at which you think the image 🖼️ (right) was captured! </h3>
<h3>⚠️ Your selection is final!</h3>
<h3> Click next to move to the next image. </h3>
"""
css = """
@font-face {
  font-family: custom;
  src: url("/file=custom.ttf");
}

h1 {
    text-align: center;
    display:block;
    font-family: custom;
}
img {
    text-align: center;
    display:block;
}
h2 {
    text-align: center;
    display:block;
    font-family: custom;
}
h3 {
    text-align: center;
    display:block;
    font-family: custom;
    font-weight: normal;
}
"""

space_js = """
<script src="https://cdn.jsdelivr.net/npm/@rapideditor/country-coder@5.2/dist/country-coder.iife.min.js"></script>
<script>
function shortcuts(e) {
    var event = document.all ? window.event : e;
    switch (e.target.tagName.toLowerCase()) {
        case "input":
        case "textarea":
        break;
        default:
        if (e.key.toLowerCase() == " " && !e.shiftKey) {
            document.getElementById("latlon_btn").click();
        }
    }
}

function shortcuts_exit(e) {
    var event = document.all ? window.event : e;
    switch (e.target.tagName.toLowerCase()) {
        case "input":
        case "textarea":
        break;
        default:
        if (e.key.toLowerCase() == "e" && e.shiftKey) {
            document.getElementById("exit_btn").click();
        }
    }
}
document.addEventListener('keypress', shortcuts, false);
document.addEventListener('keypress', shortcuts_exit, false);
</script>
"""


def inject_javascript(folium_map):
    js = """
    document.addEventListener('DOMContentLoaded', function() {
        map_name_1.on('click', function(e) {
            window.state_data = e.latlng
        });
    });
    """
    folium_map.get_root().html.add_child(Element(f'<script>{js}</script>'))

def make_map_(name="map_name", id="1"):
    map = Map(location=[39, 23], zoom_start=1)
    map._name, map._id = name, id

    LatLngPopup().add_to(map)
    inject_javascript(map)
    return map

def make_map(name="map_name", id="1"):
    map = make_map_(name, id)
    fol = Folium(value=map, height=400, visible=False, elem_id='map-fol')
    return fol

def map_js():
    return  """
    (a, textBox) => {
        const iframeMap = document.getElementById('map-fol').getElementsByTagName('iframe')[0];
        const latlng = iframeMap.contentWindow.state_data;
        if (!latlng) { return; }
        textBox = `${latlng.lat},${latlng.lng}`;
        document.getElementById('coords-tbox').getElementsByTagName('textarea')[0].value = textBox;
        var a = countryCoder.iso1A2Code([latlng.lat, latlng.lng]);
        if (!a) { a = 'nan'; }
        return [a, `${latlng.lat},${latlng.lng},${a}`];
    }
    """

def haversine(lat1, lon1, lat2, lon2):
    if (lat1 is None) or (lon1 is None) or (lat2 is None) or (lon2 is None):
        return 0
    R = 6371  # radius of the earth in km
    dLat = radians(lat2 - lat1)
    dLon = radians(lon2 - lon1)
    a = (
        sin(dLat / 2.0) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dLon / 2.0) ** 2
    )
    c = 2 * asin(sqrt(a))
    distance = R * c
    return distance

def geoscore(d):
    return 5000 * exp(-d / 1492.7)

def compute_scores(csv_file):
    df = pd.read_csv(csv_file)
    if True: #'accuracy_country' not in df.columns:
        print('Computing scores... (this may take a while)')
        geocoders = rg.search([(row.true_lat, row.true_lon) for row in df.itertuples(name='Pandas')])
        df['city'] = [geocoder['name'] for geocoder in geocoders]
        df['area'] = [geocoder['admin2'] for geocoder in geocoders]
        df['region'] = [geocoder['admin1'] for geocoder in geocoders]
        df['country'] = [geocoder['cc'] for geocoder in geocoders]
        print(list(zip((geocoders['cc']), [(row.true_lat, row.true_lon) for row in df.itertuples(name='Pandas')])))

        df['city_val'] = df['city'].apply(lambda x: 0 if pd.isna(x) else 1)
        df['area_val'] = df['area'].apply(lambda x: 0 if pd.isna(x) else 1)
        df['region_val'] = df['region'].apply(lambda x: 0 if pd.isna(x) else 1)
        df['country_val'] = df['country'].apply(lambda x: 0 if pd.isna(x) else 1)

        df['distance'] = df.apply(lambda row: haversine(row['true_lat'], row['true_lon'], row['pred_lat'], row['pred_lon']), axis=1)
        df['score'] = df.apply(lambda row: geoscore(row['distance']), axis=1)
        df['distance_base'] = df.apply(lambda row: haversine(row['true_lat'], row['true_lon'], row['pred_lat_base'], row['pred_lon_base']), axis=1)
        df['score_base'] = df.apply(lambda row: geoscore(row['distance_base']), axis=1)

        print('Computing geocoding accuracy (base)...')
        geocoders_base = rg.search([(row.pred_lat_base, row.pred_lon_base) for row in df.itertuples(name='Pandas')])
        df['pred_city_base'] = [geocoder['name'] for geocoder in geocoders_base]
        df['pred_area_base'] = [geocoder['admin2'] for geocoder in geocoders_base]
        df['pred_region_base'] = [geocoder['admin1'] for geocoder in geocoders_base]
        df['pred_country_base'] = [geocoder['cc'] for geocoder in geocoders_base]
    
        df['city_hit_base'] = [df['city'].iloc[i] != 'nan' and df['pred_city_base'].iloc[i] == df['city'].iloc[i] for i in range(len(df))]
        df['area_hit_base'] = [df['area'].iloc[i] != 'nan' and df['pred_area_base'].iloc[i] == df['area'].iloc[i] for i in range(len(df))]
        df['region_hit_base'] = [df['region'].iloc[i] != 'nan' and df['pred_region_base'].iloc[i] == df['region'].iloc[i] for i in range(len(df))]
        df['country_hit_base'] = [df['country'].iloc[i] != 'nan' and df['pred_country_base'].iloc[i] == df['country'].iloc[i] for i in range(len(df))]

        df['accuracy_city_base'] = [(0 if df['city_val'].iloc[:i].sum() == 0 else df['city_hit_base'].iloc[:i].sum()/df['city_val'].iloc[:i].sum()) for i in range(len(df))]
        df['accuracy_area_base'] = [(0 if df['area_val'].iloc[:i].sum() == 0 else df['area_hit_base'].iloc[:i].sum()/df['area_val'].iloc[:i].sum()) for i in range(len(df))]
        df['accuracy_region_base'] = [(0 if df['region_val'].iloc[:i].sum() == 0 else df['region_hit_base'].iloc[:i].sum()/df['region_val'].iloc[:i].sum()) for i in range(len(df))]
        df['accuracy_country_base'] = [(0 if df['country_val'].iloc[:i].sum() == 0 else df['country_hit_base'].iloc[:i].sum()/df['country_val'].iloc[:i].sum()) for i in range(len(df))]

        print('Computing geocoding accuracy (best)...')
        geocoders = rg.search([(row.pred_lat, row.pred_lon) for row in df.itertuples()])
        df['pred_city'] = [geocoder['name'] for geocoder in geocoders]
        df['pred_area'] = [geocoder['admin2'] for geocoder in geocoders]
        df['pred_region'] = [geocoder['admin1'] for geocoder in geocoders]
        df['pred_country'] = [geocoder['cc'] for geocoder in geocoders]
    
        df['city_hit'] = [df['city'].iloc[i] != 'nan' and df['pred_city'].iloc[i] == df['city'].iloc[i] for i in range(len(df))]
        df['area_hit'] = [df['area'].iloc[i] != 'nan' and df['pred_area'].iloc[i] == df['area'].iloc[i] for i in range(len(df))]
        df['region_hit'] = [df['region'].iloc[i] != 'nan' and df['pred_region'].iloc[i] == df['region'].iloc[i] for i in range(len(df))]
        df['country_hit'] = [df['country'].iloc[i] != 'nan' and df['pred_country'].iloc[i] == df['country'].iloc[i] for i in range(len(df))]

        df['accuracy_city'] = [(0 if df['city_val'].iloc[:i].sum() == 0 else df['city_hit_base'].iloc[:i].sum()/df['city_val'].iloc[:i].sum()) for i in range(len(df))]
        df['accuracy_area'] = [(0 if df['area_val'].iloc[:i].sum() == 0 else df['area_hit_base'].iloc[:i].sum()/df['area_val'].iloc[:i].sum()) for i in range(len(df))]
        df['accuracy_region'] = [(0 if df['region_val'].iloc[:i].sum() == 0 else df['region_hit_base'].iloc[:i].sum()/df['region_val'].iloc[:i].sum()) for i in range(len(df))]
        df['accuracy_country'] = [(0 if df['country_val'].iloc[:i].sum() == 0 else df['country_hit_base'].iloc[:i].sum()/df['country_val'].iloc[:i].sum()) for i in range(len(df))]
        df.to_csv(csv_file, index=False)


class Engine(object):
    def __init__(self, image_folder, csv_file, cache_path):
        self.image_folder = image_folder
        self.csv_file = csv_file
        self.load_images_and_coordinates(csv_file)
        self.cache_path = cache_path
          
        # Initialize the score and distance lists
        self.index = 0
        self.stats = defaultdict(list)

        # Create the figure and canvas only once
        self.fig = plt.Figure(figsize=(10, 6))
        self.ax = self.fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
        self.MIN_LON, self.MAX_LON, self.MIN_LAT, self.MAX_LAT = self.ax.get_extent()

    def load_images_and_coordinates(self, csv_file):
        # Load the CSV
        df = pd.read_csv(csv_file)

        # Get the image filenames and their coordinates
        self.images = [os.path.join(self.image_folder, f"{img_path}.jpg") for img_path in df['id'].tolist()[:]]
        self.coordinates = df[['true_lon', 'true_lat']].values.tolist()[:]

        # compute the admins
        self.df = df
        self.admins = self.df[['city', 'area', 'region', 'country']].values.tolist()[:]
        self.preds = self.df[['pred_lon', 'pred_lat']].values.tolist()[:]

    def isfinal(self):
        return self.index == len(self.images)-1

    def load_image(self):
        if self.index > len(self.images)-1:          
            self.master.update_idletasks()
            self.finish()

        self.set_clock()
        return self.images[self.index], '### ' + str(self.index + 1) + '/' + str(len(self.images))

    def get_figure(self):
        img_buf = io.BytesIO()
        self.fig.savefig(img_buf, format='png', bbox_inches='tight', pad_inches=0, dpi=300)
        pil = Image.open(img_buf)
        self.width, self.height = pil.size
        return pil

    def normalize_pixels(self, click_lon, click_lat):
        return self.MIN_LON + click_lon * (self.MAX_LON-self.MIN_LON) / self.width, self.MIN_LAT + (self.height - click_lat+1) * (self.MAX_LAT-self.MIN_LAT) / self.height

    def set_clock(self):
        self.time = time.time()

    def get_clock(self):
        return time.time() - self.time

    def click(self, click_lon, click_lat, country):
        time_elapsed = self.get_clock()
        self.stats['times'].append(time_elapsed)

        # convert click_lon, click_lat to lat, lon (given that you have the borders of the image)
        # click_lon and click_lat is in pixels
        # lon and lat is in degrees
        # click_lon, click_lat = self.normalize_pixels(click_lon, click_lat)
        self.stats['clicked_locations'].append((click_lat, click_lon))
        true_lon, true_lat = self.coordinates[self.index]
        pred_lon, pred_lat = self.preds[self.index]

        self.ax.clear()
        self.ax.set_global()
        self.ax.stock_img()
        self.ax.add_feature(cfeature.COASTLINE)
        self.ax.add_feature(cfeature.BORDERS, linestyle=':')

        self.ax.plot(pred_lon, pred_lat, 'gv', transform=ccrs.Geodetic(), label='model')
        self.ax.plot([true_lon, pred_lon], [true_lat, pred_lat], color='green', linewidth=1, transform=ccrs.Geodetic())
        self.ax.plot(click_lon, click_lat, 'bo', transform=ccrs.Geodetic(), label='user')
        self.ax.plot([true_lon, click_lon], [true_lat, click_lat], color='blue', linewidth=1, transform=ccrs.Geodetic())
        self.ax.plot(true_lon, true_lat, 'rx', transform=ccrs.Geodetic(), label='g.t.')
        legend = self.ax.legend(ncol=3, loc='lower center') #, bbox_to_anchor=(0.5, -0.15), borderaxespad=0.
        legend.get_frame().set_alpha(None)
        # legend.get_frame().set_facecolor((1, 1, 1, 0.1))
        self.fig.canvas.draw()

        distance = haversine(true_lat, true_lon, click_lat, click_lon)
        score = geoscore(distance)
        self.stats['scores'].append(score)
        self.stats['distances'].append(distance)

        # click = rg.search([(click_lat, click_lon)])[0]
        # self.stats['city'].append(int(self.admins[self.index][0] != 'nan' and click['name'] == self.admins[self.index][0]))
        # self.stats['area'].append(int(self.admins[self.index][1] != 'nan' and click['admin2'] == self.admins[self.index][1]))
        # self.stats['region'].append(int(self.admins[self.index][2] != 'nan' and click['admin1'] == self.admins[self.index][2]))
        self.stats['country'].append(int(self.admins[self.index][3] != 'nan' and country == self.admins[self.index][3]))

        df = pd.DataFrame([self.get_model_average(who) for who in ['human', 'best', 'base']], columns=['who', 'GeoScore', 'Distance', 'Accuracy (country)']).round(2)
        print(df)
        result_text = (f"### GeoScore: {score:.0f}, distance: {distance:.0f} km")

        self.cache(self.index+1, score, distance, (click_lat, click_lon), time_elapsed)
        return self.get_figure(), result_text, df

    def next_image(self):
        # Go to the next image
        self.index += 1
        return self.load_image()

    def get_model_average(self, which, all=False):
        aux, i = [], self.index+1
        if which == 'human':
            avg_score = sum(self.stats['scores']) / len(self.stats['scores']) if self.stats['scores'] else 0
            avg_distance = sum(self.stats['distances']) / len(self.stats['distances']) if self.stats['distances'] else 0
            avg_country_accuracy = (0 if self.df['country_val'].iloc[:i].sum() == 0 else sum(self.stats['country'])/self.df['country_val'].iloc[:i].sum())
            if all:
                avg_city_accuracy = (0 if self.df['city_val'].iloc[:i].sum() == 0 else sum(self.stats['city'])/self.df['city_val'].iloc[:i].sum())
                avg_area_accuracy = (0 if self.df['area_val'].iloc[:i].sum() == 0 else sum(self.stats['area'])/self.df['area_val'].iloc[:i].sum())
                avg_region_accuracy = (0 if self.df['region_val'].iloc[:i].sum() == 0 else sum(self.stats['region'])/self.df['region_val'].iloc[:i].sum())
                aux = [avg_city_accuracy, avg_area_accuracy, avg_region_accuracy]
        elif which == 'base':
            avg_score = np.mean(self.df[['score_base']].iloc[:i])
            avg_distance = np.mean(self.df[['distance_base']].iloc[:i])
            avg_country_accuracy = self.df['accuracy_country_base'].iloc[i]
            if all:
                aux = [self.df[['accuracy_city_base']].iloc[i], self.df[['accuracy_area_base']].iloc[i], self.df[['accuracy_region_base']].iloc[i]]
        elif which == 'best':
            avg_score = np.mean(self.df[['score']].iloc[:i])
            avg_distance = np.mean(self.df[['distance']].iloc[:i])
            avg_country_accuracy = self.df['accuracy_country'].iloc[i]
            print('avg_country_accuracy', avg_country_accuracy)
            if all:
                aux = [self.df[['accuracy_city_base']].iloc[i], self.df[['accuracy_area_base']].iloc[i], self.df[['accuracy_region_base']].iloc[i]]
        return [which, avg_score, avg_distance, avg_country_accuracy] + aux

    def update_average_display(self):
        # Calculate the average values
        avg_score = sum(self.stats['scores']) / len(self.stats['scores']) if self.stats['scores'] else 0
        avg_distance = sum(self.stats['distances']) / len(self.stats['distances']) if self.stats['distances'] else 0

        # Update the text box
        return f"GeoScore: {avg_score:.0f}, Distance: {avg_distance:.0f} km"
    
    def finish(self):
        click = rg.search(self.stats['clicked_locations'])
        self.stats['city'] = [(int(self.admins[self.index][0] != 'nan' and click['name'] == self.admins[self.index][0]))]
        self.stats['area'] = [(int(self.admins[self.index][1] != 'nan' and click['admin2'] == self.admins[self.index][1]))]
        self.stats['region'] = [(int(self.admins[self.index][2] != 'nan' and click['admin1'] == self.admins[self.index][2]))]
        
        df = pd.DataFrame([self.get_model_average(who, True) for who in ['human', 'best', 'base']], columns=['who', 'GeoScore', 'Distance'])
        self.cache_final(df)
        return df
        
    # Function to save the game state
    def cache(self, index, score, distance, location, time_elapsed):
        if not os.path.exists(self.cache_path):
            os.makedirs(self.cache_path)

        with open(join(self.cache_path, str(index).zfill(2) + '.txt'), 'w') as f:
            print(f"{score}, {distance}, {location[0]}, {location[1]}, {time_elapsed}", file=f)

    # Function to save the game state
    def cache_final(self, final_results):
        times = ', '.join(map(str, self.stats['times']))
        fname = join(self.cache_path, 'full.txt')
        with open(fname, 'w') as f:
            print(f"{final_results}" + '\n Times: ' + times, file=f)

        zip_ = self.cache_path.rstrip('/') + '.zip'
        archived = shutil.make_archive(self.cache_path.rstrip('/'), 'zip', self.cache_path)
        try:
            wandb.init(project="plonk")
            artifact = wandb.Artifact('results', type='results')
            artifact.add_file(zip_)
            wandb.log_artifact(artifact)
            wandb.finish()
        except Exception:
            print("Failed to log results to wandb")
            pass

        if os.path.isfile(zip_):
            os.remove(zip_)

def make_page(engine):
    i = engine.index + 1
    total = len(engine.images)
    return f"<h3>{i}/{total}</h3>"


if __name__ == "__main__":
    # login with the key from secret
    wandb.login()
    if 'csv' in os.environ:
        csv_str = os.environ['csv']
        with open(CSV_FILE, 'w') as f:
            f.write(csv_str)
    
    compute_scores(CSV_FILE)
    import gradio as gr
    def click(state, coords):
        if coords == '-1' or state['clicked']:
            return gr.update(), gr.update(), gr.update(), gr.update()
        lat, lon, country = coords.split(',')
        state['clicked'] = True
        image, text, df = state['engine'].click(float(lon), float(lat), country)
        df = df.sort_values(by='GeoScore', ascending=False)
        return gr.update(visible=False), gr.update(value=image, visible=True), gr.update(value=text), gr.update(value=df, visible=True)

    def exit_(state):
        df = state['engine'].finish()
        return gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(value=text), gr.update(visible=False), gr.update(value=df, visible=True), gr.update(value="-1", visible=False), gr.update(text=f"<h1> Your stats on OSV-5M🌍 </h1>", visible=True), gr.update(text='<h3>Thanks for playing ❤️</h3>', visible=True)

    def next_(state):
        if state['clicked']:
            if state['engine'].isfinal():
                return exit_(state)
            else:
                image, text = state['engine'].next_image()
                state['clicked'] = False
                return gr.update(value=make_map_(), visible=True), gr.update(visible=False), gr.update(value=image), gr.update(value=text), gr.update(), gr.update(), gr.update(visible=False), gr.update(value="-1"), gr.update(), gr.update()
        else:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    def start(state):
        # create a unique random temporary name under CACHE_DIR
        # generate random hex and make sure it doesn't exist under CACHE_DIR
        while True:
            path = str(uuid.uuid4().hex)
            name = os.path.join(RESULTS_DIR, path)
            if not os.path.exists(name):
                break

        state['engine'] = Engine(IMAGE_FOLDER, CSV_FILE, name)
        state['clicked'] = False
        image, text = state['engine'].load_image()

        return (
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(value=image, visible=True),
            gr.update(value=text, visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(value="<h1>OSV-5M (plonk)</h1>"),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(value="-1"),
            gr.update(visible=True),
        )

 # 
    with gr.Blocks(css=css, head=space_js) as demo:
        state = gr.State({})
        rules = gr.Markdown(RULES, visible=True)

        exit_button = gr.Button("Exit", visible=False, elem_id='exit_btn')
        start_button = gr.Button("Start", visible=True)
        with gr.Row():
            map_ = make_map()
            results = gr.Image(label='Results', visible=False)
            image_ = gr.Image(label='Image', visible=False)
        with gr.Row():
            text = gr.Markdown("", visible=False)
            text_count = gr.Markdown("", visible=False)

        with gr.Row():
            # map related
            select_button = gr.Button("Choose", elem_id='latlon_btn', visible=False)
            ####
            next_button = gr.Button("Next", visible=False, elem_id='next')
        perf = gr.Dataframe(value=None, visible=False)
        text_end = gr.Markdown("", visible=False)
    
        # map related
        coords = gr.Textbox(value="-1", label="Latitude, Longitude", visible=False, elem_id='coords-tbox')
        ####

        start_button.click(start, inputs=[state], outputs=[map_, results, image_, text_count, text, next_button, rules, state, start_button, coords, select_button])
        select_button.click(click, inputs=[state, coords], outputs=[map_, results, text, perf], js=map_js())
        next_button.click(next_, inputs=[state], outputs=[map_, results, image_, text_count, text, next_button, perf, coords, rules, text_end])
        exit_button.click(exit_, inputs=[state], outputs=[map_, results, image_, text_count, text, next_button, perf, coords, rules, text_end])

    demo.launch(allowed_paths=["custom.ttf"], debug=True)