/**
 * Russian names for OSM tag keys and values, for display only: the file keeps the
 * OSM tokens (`highway=residential`), the editor shows «Класс дороги: жилая улица».
 */

export const KEY_RU: Record<string, string> = {
  highway: 'Класс дороги', name: 'Название', 'name:ru': 'Название (рус.)', 'name:en': 'Название (англ.)',
  official_name: 'Официальное название', old_name: 'Старое название', alt_name: 'Другое название',
  ref: 'Номер', lanes: 'Полос', 'lanes:forward': 'Полос по ходу', 'lanes:backward': 'Полос против хода',
  'lanes:both_ways': 'Общих полос', oneway: 'Одностороннее движение', maxspeed: 'Ограничение скорости',
  minspeed: 'Минимальная скорость', surface: 'Покрытие', smoothness: 'Ровность', width: 'Ширина, м',
  'turn:lanes': 'Направления по полосам', 'turn:lanes:forward': 'Направления по полосам (по ходу)',
  'turn:lanes:backward': 'Направления по полосам (против хода)', bridge: 'Мост', tunnel: 'Тоннель',
  layer: 'Уровень (слой)', level: 'Этаж/уровень', junction: 'Тип перекрёстка', crossing: 'Тип перехода',
  crossing_ref: 'Вид перехода', 'crossing:markings': 'Разметка перехода', 'crossing:island': 'Островок безопасности',
  traffic_signals: 'Светофор', 'traffic_signals:direction': 'Направление светофора',
  'traffic_signals:sound': 'Звуковой сигнал', button_operated: 'Вызывная кнопка',
  access: 'Доступ', motor_vehicle: 'Моторный транспорт', motorcar: 'Легковые', vehicle: 'Транспорт',
  psv: 'Общественный транспорт', bus: 'Автобусы', trolleybus: 'Троллейбусы', tram: 'Трамваи', hgv: 'Грузовики',
  foot: 'Пешеходы', bicycle: 'Велосипеды', horse: 'Лошади', motorcycle: 'Мотоциклы', moped: 'Мопеды',
  wheelchair: 'Доступно на коляске', sidewalk: 'Тротуар', 'sidewalk:both': 'Тротуар с обеих сторон',
  'sidewalk:left': 'Тротуар слева', 'sidewalk:right': 'Тротуар справа', lit: 'Освещение',
  overtaking: 'Обгон', priority_road: 'Главная дорога', 'parking:lane:both': 'Парковка вдоль дороги',
  'parking:both': 'Парковка вдоль дороги', cycleway: 'Велодорожка', footway: 'Тип тротуара', service: 'Тип проезда',
  railway: 'Железная дорога', public_transport: 'Общественный транспорт', shelter: 'Навес', bench: 'Скамейка',
  network: 'Сеть', operator: 'Оператор', route: 'Вид маршрута', type: 'Тип отношения', restriction: 'Запрет',
  building: 'Здание', 'building:levels': 'Этажей', 'building:flats': 'Квартир', 'building:part': 'Часть здания',
  height: 'Высота, м', min_height: 'Высота низа, м', roof: 'Крыша', 'roof:shape': 'Форма крыши',
  'addr:street': 'Улица', 'addr:housenumber': 'Номер дома', 'addr:city': 'Город', 'addr:postcode': 'Индекс',
  amenity: 'Объект инфраструктуры', shop: 'Магазин', landuse: 'Землепользование', leisure: 'Отдых', natural: 'Природа',
  barrier: 'Препятствие', area: 'Площадь (замкнутый контур)', covered: 'Под навесом', incline: 'Уклон',
  'uds:planned': 'Проектный объект', 'uds:fringe': 'Связь на краю выгрузки', start_date: 'Дата постройки',
  opening_hours: 'Часы работы', phone: 'Телефон', website: 'Сайт', note: 'Примечание', description: 'Описание',
  source: 'Источник', fixme: 'Исправить',
};

/** Values shown as yes/no: these keys get a «да / нет» switch. */
const BOOL_VALUES = new Set(['yes', 'no']);

const VALUE_RU_COMMON: Record<string, string> = {
  yes: 'да', no: 'нет', designated: 'назначено', destination: 'только к цели', private: 'частный',
  permissive: 'разрешено владельцем', customers: 'для клиентов', delivery: 'для доставки', unknown: 'неизвестно',
  both: 'в обе стороны', forward: 'по ходу', backward: 'против хода', left: 'слева', right: 'справа',
  separate: 'отдельно', none: 'нет',
};

export const VALUE_RU: Record<string, Record<string, string>> = {
  highway: {
    motorway: 'автомагистраль', trunk: 'скоростная', primary: 'магистральная', secondary: 'районная',
    tertiary: 'местная', unclassified: 'без класса', residential: 'жилая улица', living_street: 'жилая зона',
    service: 'проезд', pedestrian: 'пешеходная', footway: 'тротуар', path: 'тропа', cycleway: 'велодорожка',
    steps: 'лестница', track: 'грунтовая', motorway_link: 'съезд с магистрали', trunk_link: 'съезд',
    primary_link: 'съезд', secondary_link: 'съезд', tertiary_link: 'съезд', traffic_signals: 'светофор',
    crossing: 'переход', bus_stop: 'остановка', stop: 'знак «Стоп»', give_way: 'уступи дорогу',
    turning_circle: 'разворотная площадка', street_lamp: 'фонарь', construction: 'строится',
  },
  oneway: { yes: 'да, по направлению линии', no: 'нет, в обе стороны', '-1': 'да, против направления линии' },
  surface: {
    asphalt: 'асфальт', concrete: 'бетон', paving_stones: 'тротуарная плитка', sett: 'брусчатка',
    compacted: 'уплотнённое покрытие', gravel: 'гравий', unpaved: 'без покрытия', paved: 'твёрдое покрытие',
    ground: 'грунт', dirt: 'грунт', grass: 'трава', sand: 'песок', wood: 'дерево', metal: 'металл',
  },
  junction: { roundabout: 'круговое движение', circular: 'кольцо' },
  crossing: {
    marked: 'размеченный', traffic_signals: 'регулируемый', uncontrolled: 'нерегулируемый',
    unmarked: 'без разметки', zebra: 'зебра', no: 'запрещён',
  },
  bridge: { yes: 'да', viaduct: 'виадук', no: 'нет' },
  service: { driveway: 'к дому', parking_aisle: 'проезд парковки', alley: 'переулок', 'drive-through': 'проезд-окно' },
  footway: { sidewalk: 'тротуар вдоль дороги', crossing: 'пешеходный переход' },
  railway: { tram: 'трамвай', rail: 'железная дорога', platform: 'платформа', level_crossing: 'переезд',
    tram_stop: 'остановка трамвая', crossing: 'переход через пути' },
  building: { yes: 'здание', apartments: 'жилой дом', house: 'частный дом', residential: 'жилое',
    commercial: 'коммерческое', retail: 'торговое', office: 'офисное', industrial: 'промышленное',
    school: 'школа', university: 'университет', kindergarten: 'детский сад', hospital: 'больница',
    garage: 'гараж', garages: 'гаражи', shed: 'сарай', church: 'церковь', public: 'общественное' },
  route: { bus: 'автобус', trolleybus: 'троллейбус', tram: 'трамвай', road: 'дорога' },
  type: { route: 'маршрут', multipolygon: 'мультиполигон', restriction: 'запрет поворота', public_transport: 'остановочный пункт' },
  restriction: { no_left_turn: 'нет поворота налево', no_right_turn: 'нет поворота направо',
    no_u_turn: 'нет разворота', no_straight_on: 'нет проезда прямо', only_left_turn: 'только налево',
    only_right_turn: 'только направо', only_straight_on: 'только прямо' },
};

/** Russian name of a key, or null when there is none (unknown keys keep the OSM key). */
export function keyRu(key: string): string | null {
  return KEY_RU[key] ?? null;
}

/** Russian name of a value of `key`, or null. Covers yes/no and the other common words for any key. */
export function valueRu(key: string, value: string): string | null {
  return VALUE_RU[key]?.[value] ?? VALUE_RU_COMMON[value] ?? null;
}

/** Free-text keys: never a dropdown, even when the value happens to be «yes». */
const FREE_TEXT = new Set(['name', 'name:ru', 'name:en', 'official_name', 'old_name', 'alt_name', 'ref', 'lanes',
  'lanes:forward', 'lanes:backward', 'maxspeed', 'minspeed', 'width', 'height', 'min_height', 'layer', 'level',
  'turn:lanes', 'turn:lanes:forward', 'turn:lanes:backward', 'building:levels', 'building:flats',
  'addr:street', 'addr:housenumber', 'addr:city', 'addr:postcode', 'opening_hours', 'phone', 'website', 'note',
  'description', 'source', 'fixme', 'start_date', 'network', 'operator']);

const ACCESS_VALUES = ['yes', 'no', 'designated', 'destination', 'private', 'permissive'];

/**
 * Values offered in a dropdown for this tag, each with its Russian label, or null
 * for a free-text field. yes/no tags (booleans) get «да / нет»; the current value
 * is always one of the options, so nothing is lost by opening the dropdown.
 */
export function choicesFor(key: string, value: string, suggestions: string[] = []): { value: string; label: string }[] | null {
  if (FREE_TEXT.has(key)) return null;
  let values: string[] = [];
  if (VALUE_RU[key]) values = Object.keys(VALUE_RU[key]);
  else if (ACCESS_KEYS.has(key)) values = ACCESS_VALUES;
  else if (BOOL_VALUES.has(value) || suggestions.every((s) => BOOL_VALUES.has(s)) && suggestions.length) values = ['yes', 'no'];
  if (!values.length) return null;
  const all = [...new Set([...values, ...suggestions, value])].filter(Boolean);
  return all.map((v) => ({ value: v, label: valueRu(key, v) ?? v }));
}

const ACCESS_KEYS = new Set(['access', 'motor_vehicle', 'motorcar', 'vehicle', 'psv', 'bus', 'trolleybus', 'tram',
  'hgv', 'foot', 'bicycle', 'horse', 'motorcycle', 'moped', 'wheelchair']);
