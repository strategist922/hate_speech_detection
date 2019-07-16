from sklearn.feature_extraction.text import TfidfVectorizer
from gensim.models import word2vec, KeyedVectors
import torch.utils.data as data
from utils import *
import torch
import torch.utils.data
import pandas as pd

GOOGLE_EMBED_DIM = 300
TWITTER_EMBED_DIM = 400
TWEET_SENTENCE_SIZE = 17  # 16 is average tweet token length
TWEET_WORD_SIZE = 20 # selected by histogram of tweet counts
FASTTEXT_EMBED_DIM = 300
EMBED_DIM = 200
NUM_CLASSES = 4
BERT_EMBEDDING_NUM = 11


class DataProvider(data.Dataset):
    """Generic data provider."""

    def __init__(self, inputs, targets, seed, transform=None, make_one_hot=False):
        self.inputs = np.array(inputs)
        self.num_classes = len(set(targets))
        self.transform = transform

        if make_one_hot:
            self.targets = self.to_one_of_k(targets)
        else:
            self.targets = np.array(targets)
        self.rng = np.random.RandomState(seed)

    def __len__(self):
        return len(self.inputs)

    def __getitem__(self, index):
        sample = self.inputs[index]
        if self.transform:
            sample = self.transform(sample)

        return sample, self.targets[index]

    def to_one_of_k(self, int_targets):
        """Converts integer coded class target to 1 of K coded targets.

        Args:
            int_targets (ndarray): Array of integer coded class targets (i.e.
                where an integer from 0 to `num_classes` - 1 is used to
                indicate which is the correct class). This should be of shape
                (num_data,).

        Returns:
            Array of 1 of K coded targets i.e. an array of shape
            (num_data, num_classes) where for each row all elements are equal
            to zero except for the column corresponding to the correct class
            which is equal to one.
        """
        one_of_k_targets = np.zeros((int_targets.shape[0], self.num_classes))
        one_of_k_targets[range(int_targets.shape[0]), int_targets] = 1
        return one_of_k_targets


class ImbalancedDatasetSampler(torch.utils.data.sampler.Sampler):
    """Samples elements randomly from a given list of indices for imbalanced dataset
    Arguments:
        indices (list, optional): a list of indices
        num_samples (int, optional): number of samples to draw
    """

    def __init__(self, dataset, indices=None, num_samples=None):

        # if indices is not provided,
        # all elements in the dataset will be considered
        self.indices = list(range(len(dataset))) \
            if indices is None else indices

        # if num_samples is not provided,
        # draw `len(indices)` samples in each iteration
        self.num_samples = len(self.indices) \
            if num_samples is None else num_samples

        # distribution of classes in the dataset
        label_to_count = {}
        for idx in self.indices:
            inputs, label = dataset[idx]
            if label in label_to_count:
                label_to_count[label] += 1
            else:
                label_to_count[label] = 1

        # weight for each sample
        weights = [1.0 / label_to_count[dataset[idx][1]] for idx in self.indices]
        self.weights = torch.DoubleTensor(weights)

    def __iter__(self):
        return (self.indices[i] for i in torch.multinomial(self.weights, self.num_samples, replacement=True))

    def __len__(self):
        return self.num_samples


class TextDataProvider(object):
    def __init__(self, path_data, path_labels, experiment_flag):
        self.experiment_flag = experiment_flag
        label_data = pd.read_csv(path_labels, header='infer', index_col=0, squeeze=True).to_dict()
        data = np.load(os.path.join(ROOT_DIR, path_data), allow_pickle=True)
        data = data[()]
        self.outputs, self.labels = extract_tweets(label_data, data, self.experiment_flag)

    @staticmethod
    def _fetch_model(tweets_corpus, key):
        if key == 'twitter':
            print("[Model] Using {} embeddings".format(key))
            embed_dim = TWITTER_EMBED_DIM
            filename = os.path.join(ROOT_DIR, 'data/word2vec_twitter_model/word2vec_twitter_model.bin')
            word_vectors = KeyedVectors.load_word2vec_format(filename, binary=True, unicode_errors='ignore')
        return word_vectors, embed_dim

    @staticmethod
    def fetch_character_symbols(raw_tweets):
        """
        Dynamically create mapping for one hot encoding of chars
        :param raw_tweets: list of all tweets
        :return:
        """
        chars = set()
        for i, tweet in enumerate(raw_tweets):
            for word in tweet:
                chars.update(list(word))
        char_mapping = {char: np.eye(len(chars))[index] for index, char in enumerate(chars)}
        return chars, char_mapping

    def process_tweet(self, tweet, embed_dim, word_vectors):
        embedded_tweet = []

        # trim if too large
        if len(tweet) >= TWEET_SENTENCE_SIZE:
            tweet = tweet[:TWEET_SENTENCE_SIZE]

        # convert all into word embeddings
        for word in tweet:
            embedding = generate_random_embedding(embed_dim) if word not in word_vectors else word_vectors[word]
            embedded_tweet.append(embedding)

        # pad if too short
        if len(tweet) < TWEET_SENTENCE_SIZE:
            diff = TWEET_SENTENCE_SIZE - len(tweet)
            embedded_tweet += [generate_random_embedding(embed_dim) for _ in range(diff)]
        return embedded_tweet

    def fetch_word_embeddings(self, outputs, word_vectors, embed_dim):
        outputs_embed = []
        for i, output in enumerate(outputs):

            # process first tweet
            embedded_tweet = self.process_tweet(output['tokens'], embed_dim, word_vectors)

            if self.experiment_flag == 2:
                #proceses second tweet
                if output['context_tweet'] is None:
                    for i in range(TWEET_SENTENCE_SIZE):
                        blank_embedding = np.zeros(embed_dim,)
                        embedded_tweet.append(blank_embedding)
                else:
                    context_embedding = self.process_tweet(output['context_tokens'], embed_dim, word_vectors)
                    for i in range(TWEET_SENTENCE_SIZE):
                        embedded_tweet.append(context_embedding[i])
                assert len(embedded_tweet) == TWEET_SENTENCE_SIZE*2

            if self.experiment_flag == 3:
                for i, embed in enumerate(embedded_tweet):
                    embedded_tweet[i] = np.concatenate((embed, [output['retweet_count'],
                                                                output['favorite_count']]))

            if self.experiment_flag == 4:
                for i, embed in enumerate(embedded_tweet):
                    embedded_tweet[i] = embed * output['retweet_count'] * output['favorite_count']

            output['embedding'] = embedded_tweet
            outputs_embed.append(output)
        return outputs_embed

    @staticmethod
    def generate_tdidf_experiment_3_embeddings(data_list, embeddings, experiment_flag):
        processed_embeddings = []
        for i, embed in enumerate(embeddings):
            embed = np.transpose(np.array(embed))
            embed = embed.reshape(-1)
            output = data_list[i]
            if experiment_flag == 3:
                processed_embeddings.append(np.concatenate((embed, [output['retweet_count'], output['favorite_count']])))
            if experiment_flag == 4:
                processed_embed = embed * output['retweet_count'] * output['favorite_count']
                processed_embeddings.append(processed_embed)
        return processed_embeddings

    def generate_tdidf_embeddings(self, seed):
        x_train, y_train, x_valid, y_valid, x_test, y_test = split_data(self.outputs, self.labels, seed)
        vectorizer = TfidfVectorizer(use_idf=True, max_features=10000)
        x_train_embed = vectorizer.fit_transform(convert_to_feature_embeddings(x_train,
                                                                               key='tokens',
                                                                               experiment_flag=self.experiment_flag
                                                                               )).todense()
        x_valid_embed = vectorizer.transform(convert_to_feature_embeddings(x_valid,
                                                                           key='tokens',
                                                                           experiment_flag=self.experiment_flag
                                                                           )).todense()
        x_test_embed = vectorizer.transform(convert_to_feature_embeddings(x_test,
                                                                          key='tokens',
                                                                          experiment_flag=self.experiment_flag
                                                                          )).todense()
        if self.experiment_flag == 3 or self.experiment_flag == 4:
           x_train_embed = self.generate_tdidf_experiment_3_embeddings(x_train, x_train_embed, self.experiment_flag)
           x_valid_embed = self.generate_tdidf_experiment_3_embeddings(x_valid, x_valid_embed, self.experiment_flag)
           x_test_embed = self.generate_tdidf_experiment_3_embeddings(x_train, x_test_embed, self.experiment_flag)

        return {'x_train': x_train_embed,
                'y_train': y_train,
                'x_valid': x_valid_embed,
                'y_valid': y_valid,
                'x_test': x_test_embed,
                'y_test': y_test}

    @staticmethod
    def generate_bert_embedding_dict():
        embeddings = {}
        for i in range(BERT_EMBEDDING_NUM):
            results = np.load(os.path.join(ROOT_DIR, 'data/bert_embeddings_{}.npz'.format(i)), allow_pickle=True)
            print("Downloading Bert, Processed {} / {}".format(i+1, BERT_EMBEDDING_NUM))
            results = results['a']
            results = results[()]
            embeddings = {**results, **embeddings}
        return embeddings

    def generate_bert_embeddings(self, embeddings, data):
        """
        :param embeddings: preprocessed bert word embeddings
        :param data: has all fields, separate fn from gen_word_embeddings
        :return:
        """
        if self.experiment_flag == 1 or self.experiment_flag == 2:
            return [embeddings[int(output['id'])] for output in data]
        elif self.experiment_flag == 2:
            # finding bert embedding for reply tweet
            data_embed = []
            for output in data:
                tweet_embed = embeddings[int(output['id'])]
                reply_status_id = int(output['in_reply_to_status_id'])
                final_embed = []
                if reply_status_id == -1 or reply_status_id not in embeddings:
                    for i in range(17):
                        blank_embedding = np.zeros(768, )
                        final_embed.append(blank_embedding)
                    final_embed = final_embed + tweet_embed
                else:
                    final_embed = final_embed + embeddings[reply_status_id]
                data_embed.append(final_embed)
            return data_embed
        elif self.experiment_flag == 3:
            data_embed = []
            for output in data:
                output_embeddings = embeddings[int(output['id'])]
                for i, embed in enumerate(output_embeddings):
                    output_embeddings[i] = np.concatenate((embed, [output['retweet_count'], output['favorite_count']]))
                data_embed.append(output_embeddings)
            return data_embed

    def generate_word_level_embeddings(self, embedding_key, seed):
        x_train, y_train, x_valid, y_valid, x_test, y_test = split_data(self.outputs, self.labels, seed)
        if embedding_key == 'bert':
            embeddings = self.generate_bert_embedding_dict()
            x_train = self.generate_bert_embeddings(embeddings, x_train)
            x_valid = self.generate_bert_embeddings(embeddings, x_valid)
            x_test = self.generate_bert_embeddings(embeddings, x_test)
        else:
            word_vectors, embed_dim = self._fetch_model(x_train, embedding_key)

            # embed inputs
            x_train_embed = self.fetch_word_embeddings(x_train, word_vectors, embed_dim)
            x_valid_embed = self.fetch_word_embeddings(x_valid, word_vectors, embed_dim)
            x_test_embed = self.fetch_word_embeddings(x_test, word_vectors, embed_dim)
            x_train = convert_to_feature_embeddings(x_train_embed, self.experiment_flag)
            x_valid = convert_to_feature_embeddings(x_valid_embed, self.experiment_flag)
            x_test = convert_to_feature_embeddings(x_test_embed, self.experiment_flag)
        print("Word embeddings generated")
        return {'x_train': x_train,
                'y_train': y_train,
                'x_valid': x_valid,
                'y_valid': y_valid,
                'x_test': x_test,
                'y_test': y_test
                }

    def generate_char_level_embeddings(self, seed):
        raw_tweets = self.tokenize(self.raw_tweets)
        processed_tweets = self.fetch_character_embeddings(raw_tweets)
        return self._generate_embedding_output(processed_tweets, seed)
