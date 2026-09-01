import moment from 'moment';
import { DateQuery } from './date-query';
import { StringQuery } from './string-query';
import { FreqQuery } from './freq-query';
import { GeoQuery } from './geo/geo-query';
import { SourceQuery } from './data-source-query';

export const queries = {
  date: {
    label: 'Date',
    component: DateQuery,
    selected: false,
    advanced: false,
    description: 'The date the document was created',
    validator: ({ from, to }) => {
      let parsedTo = moment(to);
      let parsedFrom = moment(from);
      if (parsedTo.isValid() && parsedFrom.isValid() && parsedTo.isAfter(parsedFrom)) {
        return `min_datetime=${encodeURIComponent(parsedFrom.format())}&max_datetime=${encodeURIComponent(
          parsedTo.format()
        )}`;
      }
      return false;
    },
    value: '',
  },
  geo: {
    label: 'Geolocation',
    component: GeoQuery,
    selected: false,
    advanced: false,
    description: 'lat and long with radius for geo search',
    validator: ({ lat, lon, radius, queryType }) => {
      return `${queryType}_geo=${lon},${lat},${radius}`;
    },
    value: '',
  },
  modified: {
    label: 'Modified',
    component: DateQuery,
    selected: false,
    advanced: true,
    description: 'The date the recording metadata was last modified',
    validator: ({ from, to }) => {
      let parsedTo = moment(to);
      let parsedFrom = moment(from);
      if (parsedTo.isValid() && parsedFrom.isValid() && parsedTo.isAfter(parsedFrom)) {
        return `min_modified=${encodeURIComponent(parsedFrom.format())}&max_modified=${encodeURIComponent(
          parsedTo.format()
        )}`;
      }
      return false;
    },
    value: '',
  },
  author: {
    label: 'Author',
    component: StringQuery,
    selected: false,
    advanced: false,
    description: 'Who uploaded the recording',
    validator: (author: string) => {
      if (!author) {
        return false;
      }
      return `author=${encodeURIComponent(author)}`;
    },
    value: '',
  },
  comment: {
    label: 'Comment',
    component: StringQuery,
    selected: false,
    advanced: true,
    description: 'Comments contained in the annotation',
    validator: (comment: string) => {
      if (!comment) {
        return false;
      }
      return `comment=${encodeURIComponent(comment)}`;
    },
    value: '',
  },
  description: {
    label: 'Description',
    component: StringQuery,
    selected: false,
    advanced: false,
    description: 'The global description of the recording',
    validator: (description: string) => {
      if (!description) {
        return false;
      }
      return `description=${encodeURIComponent(description)}`;
    },
    value: '',
  },
  frequency: {
    label: 'Frequency',
    component: FreqQuery,
    selected: true,
    advanced: false,
    description: 'The frequency range to search over (Hz)',
    validator: ({ from, to }) => {
      const parsedFrom: number = parseInt(from);
      const parsedTo: number = parseInt(to);
      if (parsedTo > 0 && parsedFrom < parsedTo) {
        return `min_frequency=${parsedFrom}&max_frequency=${parsedTo}`;
      }
      return false;
    },
    value: `min_frequency=30000000&max_frequency=300000000`,
  },
  container: {
    label: 'Container',
    component: StringQuery,
    selected: false,
    advanced: true,
    description: 'The container the document is in',
    validator: (container: string) => {
      if (!container) {
        return false;
      }
      return `container=${encodeURIComponent(container)}`;
    },
    value: '',
  },
  label: {
    label: 'Label',
    component: StringQuery,
    selected: false,
    advanced: true,
    description: 'The label of the document',
    validator: (label: string) => {
      if (!label) {
        return false;
      }
      return `label=${encodeURIComponent(label)}`;
    },
    value: '',
  },
  signal_type: {
    label: 'Signal Type',
    component: StringQuery,
    selected: false,
    advanced: false,
    description: 'The signal type (e.g. IRIDIUM)',
    options: ['starlink', 'iridium', 'ais', 'ads-b'],
    validator: (signalType: string) => {
      if (!signalType) {
        return false;
      }
      return `signal_type=${encodeURIComponent(signalType)}`;
    },
    value: '',
  },
  hw: {
    label: 'Hardware',
    component: StringQuery,
    selected: false,
    advanced: false,
    description: 'The hardware used to make the recording',
    options: ['bladerf', 'hackrf', 'usrp', 'rtl-sdr', 'airspy', 'limesdr', 'plutosdr', 'sdrplay'],
    validator: (hw: string) => {
      if (!hw) {
        return false;
      }
      return `hw=${encodeURIComponent(hw)}`;
    },
    value: '',
  },
  location: {
    label: 'Location',
    component: StringQuery,
    selected: false,
    advanced: false,
    description: 'The location where the recording was made (e.g. montreal)',
    validator: (location: string) => {
      if (!location) {
        return false;
      }
      return `location=${encodeURIComponent(location)}`;
    },
    value: '',
  },
  operator: {
    label: 'Operator',
    component: StringQuery,
    selected: false,
    advanced: false,
    description: 'The operator who made the recording',
    validator: (operator: string) => {
      if (!operator) {
        return false;
      }
      return `operator=${encodeURIComponent(operator)}`;
    },
    value: '',
  },
  recorder: {
    label: 'Recorder',
    component: StringQuery,
    selected: false,
    advanced: false,
    description: 'The recorder software or device used',
    validator: (recorder: string) => {
      if (!recorder) {
        return false;
      }
      return `recorder=${encodeURIComponent(recorder)}`;
    },
    value: '',
  },
  text: {
    label: 'Text',
    component: StringQuery,
    selected: false,
    advanced: false,
    description: 'Full text search across valid fields',
    validator: (text: string) => {
      if (!text) {
        return false;
      }
      return `text=${encodeURIComponent(text)}`;
    },
    value: '',
  },
  datasource: {
    label: 'Data Source',
    component: SourceQuery,
    selected: false,
    advanced: true,
    description: 'The data source the document is from',
    validator: (dataSource: string[]) => {
      if (dataSource.length === 0) {
        return '';
      }
      let queryParts: string[] = dataSource.map((item) => `databaseid=${encodeURIComponent(item)}`);
      return queryParts.join('&');
    },
    value: '',
  },
};
