"""
Face Handler V2 - Gestionnaire de reconnaissance faciale
Permet le suivi des enfants entre différentes analyses pour améliorer la confiance
Optimisé pour le hackathon Google Gemma
"""
import numpy as np
import logging
import json
import pickle
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import time

logger = logging.getLogger(__name__)

class FaceHandlerV2:
    """Gestionnaire de reconnaissance faciale pour suivi longitudinal"""
    
    def __init__(self):
        self.face_recognition_available = False
        self.known_faces = {}  # face_id -> face_data
        self.analysis_history = {}  # face_id -> [analyses]
        self.next_face_id = 1
        
        # Chemins de sauvegarde
        self.data_dir = Path("data/face_tracking")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.faces_db_path = self.data_dir / "known_faces.pkl"
        self.history_path = self.data_dir / "analysis_history.json"
        
        # Configuration
        self.config = {
            'similarity_threshold': 0.6,
            'max_history_per_face': 10,
            'confidence_boost_factor': 1.2,  # Boost de confiance pour analyses répétées
            'enable_persistence': True
        }
        
        # Statistiques
        self.stats = {
            'total_faces_processed': 0,
            'new_faces_registered': 0,
            'faces_recognized': 0,
            'confidence_boosts_applied': 0
        }
        
        # Initialiser la reconnaissance faciale
        self._initialize_face_recognition()
        
        # Charger les données existantes
        if self.config['enable_persistence']:
            self._load_data()
    
    def _initialize_face_recognition(self):
        """Initialise la reconnaissance faciale avec gestion d'erreurs"""
        try:
            import face_recognition
            self.face_recognition = face_recognition
            self.face_recognition_available = True
            logger.info("✅ Face recognition library available")
            
        except ImportError:
            logger.warning("Face recognition library not available")
            logger.info("Face tracking will work with basic computer vision")
            self.face_recognition_available = False
        except Exception as e:
            logger.error(f"Face recognition initialization failed: {e}")
            self.face_recognition_available = False
    
    def process_faces(self, image_path: str, detection_results: Dict) -> Dict:
        """
        Traite les visages détectés et gère le suivi
        
        Args:
            image_path: Chemin vers l'image
            detection_results: Résultats de détection d'yeux
            
        Returns:
            Résultats du traitement facial avec suivi
        """
        try:
            start_time = time.time()
            
            # Initialiser les résultats
            tracking_results = {
                'tracked_faces': 0,
                'new_faces': 0,
                'recognized_faces': 0,
                'face_mappings': {},  # region_id -> face_id
                'confidence_boosts': {},  # region_id -> boost_factor
                'processing_time': 0
            }
            
            if not self.face_recognition_available:
                logger.info("Face recognition not available, using basic tracking")
                return self._basic_face_tracking(detection_results, tracking_results)
            
            # Traiter chaque région détectée
            for region in detection_results.get('regions', []):
                face_result = self._process_single_region(image_path, region)
                
                if face_result:
                    region_id = region['id']
                    tracking_results['face_mappings'][region_id] = face_result['face_id']
                    
                    if face_result['is_new']:
                        tracking_results['new_faces'] += 1
                    else:
                        tracking_results['recognized_faces'] += 1
                        # Appliquer boost de confiance pour visage reconnu
                        boost_factor = self._calculate_confidence_boost(face_result['face_id'])
                        tracking_results['confidence_boosts'][region_id] = boost_factor
                        self.stats['confidence_boosts_applied'] += 1
            
            tracking_results['tracked_faces'] = len(tracking_results['face_mappings'])
            tracking_results['processing_time'] = time.time() - start_time
            
            # Sauvegarder les données
            if self.config['enable_persistence']:
                self._save_data()
            
            # Mettre à jour les statistiques
            self.stats['total_faces_processed'] += tracking_results['tracked_faces']
            self.stats['new_faces_registered'] += tracking_results['new_faces']
            self.stats['faces_recognized'] += tracking_results['recognized_faces']
            
            logger.info(f"Face processing: {tracking_results['tracked_faces']} faces, "
                       f"{tracking_results['new_faces']} new, "
                       f"{tracking_results['recognized_faces']} recognized")
            
            return tracking_results
            
        except Exception as e:
            logger.error(f"Face processing failed: {e}")
            return self._create_empty_tracking_result()
    
    def _process_single_region(self, image_path: str, region: Dict) -> Optional[Dict]:
        """Traite une seule région pour reconnaissance faciale"""
        try:
            # Estimer la position du visage à partir de la région oculaire
            bbox = region['bbox']
            estimated_face_bbox = self._estimate_face_from_eye(bbox)
            
            # Extraire l'encodage facial
            face_encoding = self._extract_face_encoding(image_path, estimated_face_bbox)
            
            if face_encoding is None:
                return None
            
            # Chercher une correspondance
            face_id, is_new = self._find_or_create_face(face_encoding, region)
            
            return {
                'face_id': face_id,
                'is_new': is_new,
                'encoding': face_encoding,
                'region_id': region['id']
            }
            
        except Exception as e:
            logger.error(f"Single region processing failed: {e}")
            return None
    
    def _estimate_face_from_eye(self, eye_bbox: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        """Estime la boîte englobante du visage à partir d'une région oculaire"""
        x, y, w, h = eye_bbox
        
        # Estimation approximative : le visage fait environ 3x la largeur de l'œil
        # et 4x la hauteur de l'œil
        face_width = int(w * 3)
        face_height = int(h * 4)
        
        # Centrer approximativement
        face_x = max(0, x - w)
        face_y = max(0, y - int(h * 1.5))
        
        return (face_x, face_y, face_width, face_height)
    
    def _extract_face_encoding(self, image_path: str, face_bbox: Tuple[int, int, int, int]) -> Optional[np.ndarray]:
        """Extrait l'encodage facial d'une région"""
        try:
            # Charger l'image
            image = self.face_recognition.load_image_file(image_path)
            
            # Convertir bbox au format face_recognition (top, right, bottom, left)
            x, y, w, h = face_bbox
            face_location = (y, x + w, y + h, x)
            
            # Extraire l'encodage
            face_encodings = self.face_recognition.face_encodings(
                image, [face_location]
            )
            
            if face_encodings:
                return face_encodings[0]
            else:
                return None
                
        except Exception as e:
            logger.error(f"Face encoding extraction failed: {e}")
            return None
    
    def _find_or_create_face(self, face_encoding: np.ndarray, region: Dict) -> Tuple[str, bool]:
        """Trouve un visage existant ou en crée un nouveau"""
        try:
            # Chercher parmi les visages connus
            best_match_id = None
            best_distance = float('inf')
            
            for face_id, face_data in self.known_faces.items():
                known_encoding = face_data['encoding']
                
                # Calculer la distance
                distance = np.linalg.norm(face_encoding - known_encoding)
                
                if distance < best_distance:
                    best_distance = distance
                    best_match_id = face_id
            
            # Vérifier si la correspondance est assez bonne
            if best_match_id and best_distance < (1 - self.config['similarity_threshold']):
                # Visage reconnu
                self.known_faces[best_match_id]['last_seen'] = datetime.now().isoformat()
                self.known_faces[best_match_id]['seen_count'] += 1
                return best_match_id, False
            else:
                # Nouveau visage
                face_id = f"child_{self.next_face_id:04d}"
                self.next_face_id += 1
                
                self.known_faces[face_id] = {
                    'encoding': face_encoding,
                    'first_seen': datetime.now().isoformat(),
                    'last_seen': datetime.now().isoformat(),
                    'seen_count': 1,
                    'region_types': [region.get('type', 'unknown')],
                    'metadata': {
                        'detection_source': region.get('source', 'unknown')
                    }
                }
                
                self.analysis_history[face_id] = []
                
                return face_id, True
                
        except Exception as e:
            logger.error(f"Face matching failed: {e}")
            # Créer un ID de fallback
            fallback_id = f"fallback_{int(time.time())}"
            return fallback_id, True
    
    def _calculate_confidence_boost(self, face_id: str) -> float:
        """Calcule le boost de confiance pour un visage reconnu"""
        try:
            if face_id not in self.analysis_history:
                return 1.0
            
            history = self.analysis_history[face_id]
            seen_count = self.known_faces.get(face_id, {}).get('seen_count', 1)
            
            # Plus l'enfant a été vu et analysé, plus le boost est important
            base_boost = min(1.5, 1.0 + (seen_count - 1) * 0.1)
            
            # Bonus si des analyses précédentes ont détecté quelque chose
            positive_analyses = sum(1 for analysis in history 
                                  if analysis.get('has_positive_findings', False))
            
            if positive_analyses > 0:
                consistency_boost = min(1.3, 1.0 + positive_analyses * 0.15)
                return base_boost * consistency_boost
            
            return base_boost
            
        except Exception as e:
            logger.error(f"Confidence boost calculation failed: {e}")
            return 1.0
    
    def add_analysis_result(self, face_id: str, analysis_results: Dict, image_path: str):
        """Ajoute un résultat d'analyse à l'historique d'un visage"""
        try:
            if face_id not in self.analysis_history:
                self.analysis_history[face_id] = []
            
            # Détecter s'il y a des findings positifs
            has_positive = any(
                result.get('leukocoria_detected', False) 
                for result in analysis_results.get('results', [])
            )
            
            # Créer l'entrée d'historique
            history_entry = {
                'timestamp': datetime.now().isoformat(),
                'image_path': image_path,
                'has_positive_findings': has_positive,
                'analysis_summary': {
                    'regions_analyzed': analysis_results.get('regions_analyzed', 0),
                    'positive_detections': sum(1 for r in analysis_results.get('results', []) 
                                             if r.get('leukocoria_detected', False)),
                    'method': analysis_results.get('method', 'unknown')
                }
            }
            
            # Ajouter à l'historique
            self.analysis_history[face_id].append(history_entry)
            
            # Limiter la taille de l'historique
            max_history = self.config['max_history_per_face']
            if len(self.analysis_history[face_id]) > max_history:
                self.analysis_history[face_id] = self.analysis_history[face_id][-max_history:]
            
            logger.info(f"Analysis result added to face {face_id} history")
            
        except Exception as e:
            logger.error(f"Failed to add analysis result: {e}")
    
    def get_face_analysis_summary(self, face_id: str) -> Dict:
        """Retourne un résumé des analyses pour un visage"""
        try:
            if face_id not in self.analysis_history:
                return {'error': 'Face not found'}
            
            history = self.analysis_history[face_id]
            face_data = self.known_faces.get(face_id, {})
            
            if not history:
                return {
                    'face_id': face_id,
                    'total_analyses': 0,
                    'positive_analyses': 0,
                    'first_seen': face_data.get('first_seen'),
                    'last_seen': face_data.get('last_seen'),
                    'recommendation': 'No analysis history available'
                }
            
            # Calculer les statistiques
            total_analyses = len(history)
            positive_analyses = sum(1 for entry in history if entry['has_positive_findings'])
            consistency_rate = (positive_analyses / total_analyses) * 100 if total_analyses > 0 else 0
            
            # Déterminer la recommandation
            if positive_analyses >= 2:
                recommendation = "URGENT: Multiple positive findings - immediate medical consultation required"
                urgency = "immediate"
            elif positive_analyses == 1 and total_analyses >= 2:
                recommendation = "MONITOR: One positive finding - follow-up recommended"
                urgency = "soon"
            elif consistency_rate > 50:
                recommendation = "EVALUATE: Concerning pattern - medical evaluation advised"
                urgency = "urgent"
            else:
                recommendation = "CONTINUE: Regular monitoring - no immediate concerns"
                urgency = "routine"
            
            return {
                'face_id': face_id,
                'total_analyses': total_analyses,
                'positive_analyses': positive_analyses,
                'consistency_rate': consistency_rate,
                'first_seen': face_data.get('first_seen'),
                'last_seen': face_data.get('last_seen'),
                'seen_count': face_data.get('seen_count', 0),
                'recommendation': recommendation,
                'urgency': urgency,
                'recent_analyses': history[-3:] if len(history) > 3 else history
            }
            
        except Exception as e:
            logger.error(f"Face summary generation failed: {e}")
            return {'error': str(e)}
    
    def get_all_tracked_faces(self) -> List[Dict]:
        """Retourne un résumé de tous les visages suivis"""
        try:
            summaries = []
            
            for face_id in self.known_faces.keys():
                summary = self.get_face_analysis_summary(face_id)
                if 'error' not in summary:
                    summaries.append(summary)
            
            # Trier par urgence puis par nombre d'analyses positives
            urgency_order = {'immediate': 0, 'urgent': 1, 'soon': 2, 'routine': 3}
            summaries.sort(key=lambda x: (
                urgency_order.get(x.get('urgency', 'routine'), 3),
                -x.get('positive_analyses', 0),
                -x.get('total_analyses', 0)
            ))
            
            return summaries
            
        except Exception as e:
            logger.error(f"Failed to get tracked faces: {e}")
            return []
    
    def _basic_face_tracking(self, detection_results: Dict, tracking_results: Dict) -> Dict:
        """Suivi basique sans reconnaissance faciale"""
        try:
            # Assigner des IDs basiques basés sur la position
            for i, region in enumerate(detection_results.get('regions', [])):
                face_id = f"basic_{region['type']}_{i}"
                tracking_results['face_mappings'][region['id']] = face_id
                tracking_results['new_faces'] += 1
            
            tracking_results['tracked_faces'] = len(tracking_results['face_mappings'])
            
            logger.info("Using basic face tracking without recognition")
            return tracking_results
            
        except Exception as e:
            logger.error(f"Basic face tracking failed: {e}")
            return self._create_empty_tracking_result()
    
    def _create_empty_tracking_result(self) -> Dict:
        """Crée un résultat de suivi vide"""
        return {
            'tracked_faces': 0,
            'new_faces': 0,
            'recognized_faces': 0,
            'face_mappings': {},
            'confidence_boosts': {},
            'processing_time': 0,
            'error': 'Face tracking failed'
        }
    
    def _load_data(self):
        """Charge les données persistantes"""
        try:
            # Charger les visages connus
            if self.faces_db_path.exists():
                with open(self.faces_db_path, 'rb') as f:
                    data = pickle.load(f)
                    self.known_faces = data.get('known_faces', {})
                    self.next_face_id = data.get('next_face_id', 1)
                
                logger.info(f"Loaded {len(self.known_faces)} known faces")
            
            # Charger l'historique
            if self.history_path.exists():
                with open(self.history_path, 'r', encoding='utf-8') as f:
                    self.analysis_history = json.load(f)
                
                logger.info(f"Loaded analysis history for {len(self.analysis_history)} faces")
                
        except Exception as e:
            logger.error(f"Failed to load face data: {e}")
            self.known_faces = {}
            self.analysis_history = {}
            self.next_face_id = 1
    
    def _save_data(self):
        """Sauvegarde les données persistantes"""
        try:
            # Sauvegarder les visages connus
            data = {
                'known_faces': self.known_faces,
                'next_face_id': self.next_face_id,
                'saved_at': datetime.now().isoformat()
            }
            
            with open(self.faces_db_path, 'wb') as f:
                pickle.dump(data, f)
            
            # Sauvegarder l'historique
            with open(self.history_path, 'w', encoding='utf-8') as f:
                json.dump(self.analysis_history, f, indent=2, default=str)
            
            logger.debug("Face data saved successfully")
            
        except Exception as e:
            logger.error(f"Failed to save face data: {e}")
    
    def save_data(self):
        """Interface publique pour sauvegarder"""
        if self.config['enable_persistence']:
            self._save_data()
    
    def export_face_report(self, face_id: str, output_path: Optional[str] = None) -> str:
        """Exporte un rapport détaillé pour un visage"""
        try:
            summary = self.get_face_analysis_summary(face_id)
            
            if 'error' in summary:
                return f"Error: {summary['error']}"
            
            # Générer le rapport
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if output_path is None:
                output_path = self.data_dir / f"face_report_{face_id}_{timestamp}.json"
            
            # Données complètes du rapport
            full_report = {
                'report_info': {
                    'face_id': face_id,
                    'generated_at': datetime.now().isoformat(),
                    'report_type': 'longitudinal_analysis'
                },
                'face_summary': summary,
                'detailed_history': self.analysis_history.get(face_id, []),
                'face_metadata': self.known_faces.get(face_id, {}),
                'recommendations': {
                    'medical_action': summary.get('recommendation', 'Unknown'),
                    'urgency_level': summary.get('urgency', 'routine'),
                    'monitoring_advice': self._generate_monitoring_advice(summary)
                }
            }
            
            # Sauvegarder
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(full_report, f, indent=2, default=str)
            
            logger.info(f"Face report exported: {output_path}")
            return str(output_path)
            
        except Exception as e:
            logger.error(f"Face report export failed: {e}")
            return f"Export failed: {e}"
    
    def _generate_monitoring_advice(self, summary: Dict) -> str:
        """Génère des conseils de surveillance personnalisés"""
        positive_analyses = summary.get('positive_analyses', 0)
        total_analyses = summary.get('total_analyses', 0)
        urgency = summary.get('urgency', 'routine')
        
        if urgency == 'immediate':
            return ("Contact pediatric ophthalmologist immediately. Multiple positive findings "
                   "require urgent professional evaluation. Do not delay seeking medical attention.")
        
        elif urgency == 'urgent':
            return ("Schedule ophthalmologist appointment within 1-2 weeks. Concerning pattern "
                   "detected that warrants professional evaluation.")
        
        elif urgency == 'soon':
            return ("Consider ophthalmologist consultation within 1 month. Continue regular "
                   "photo monitoring and watch for any changes.")
        
        else:
            return ("Continue regular photo monitoring. Take photos monthly under good lighting "
                   "conditions. No immediate medical concerns detected.")
    
    def get_statistics(self) -> Dict:
        """Retourne les statistiques de suivi"""
        return {
            **self.stats,
            'total_known_faces': len(self.known_faces),
            'faces_with_history': len(self.analysis_history),
            'face_recognition_available': self.face_recognition_available,
            'data_persistence_enabled': self.config['enable_persistence']
        }
    
    def reset_all_data(self, confirm: bool = False):
        """Remet à zéro toutes les données (ATTENTION: irréversible)"""
        if not confirm:
            logger.warning("Reset requested but not confirmed")
            return False
        
        try:
            # Vider les données en mémoire
            self.known_faces = {}
            self.analysis_history = {}
            self.next_face_id = 1
            
            # Supprimer les fichiers
            if self.faces_db_path.exists():
                self.faces_db_path.unlink()
            if self.history_path.exists():
                self.history_path.unlink()
            
            # Réinitialiser les stats
            self.stats = {
                'total_faces_processed': 0,
                'new_faces_registered': 0,
                'faces_recognized': 0,
                'confidence_boosts_applied': 0
            }
            
            logger.info("All face tracking data has been reset")
            return True
            
        except Exception as e:
            logger.error(f"Data reset failed: {e}")
            return False
    
    def cleanup(self):
        """Nettoie les ressources"""
        try:
            # Sauvegarder avant fermeture
            if self.config['enable_persistence']:
                self._save_data()
            
            logger.info("Face handler cleaned up")
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
    
    def __del__(self):
        """Nettoyage automatique"""
        self.cleanup()

    def adjust_confidence_with_history(self, face_id: str, current_analysis_results: List[Dict]) -> List[Dict]:
        """Ajuste la confiance basée sur l'historique médical"""
        if face_id not in self.analysis_history:
            return current_analysis_results  # Pas d'historique

        try:
            history = self.analysis_history[face_id]
            if len(history) < 2:
                return current_analysis_results  # Pas assez d'historique

            # Analyser l'historique récent (30 derniers jours)
            from datetime import datetime, timedelta
            import time

            recent_cutoff = time.time() - (30 * 24 * 3600)
            recent_analyses = [
                entry for entry in history 
                if entry.get('timestamp', 0) > recent_cutoff
            ]

            if not recent_analyses:
                return current_analysis_results

            # Calculer les tendances historiques
            historical_positives = []
            historical_confidences = []

            for entry in recent_analyses:
                analysis = entry.get('analysis_summary', {})
                if analysis:
                    positive_detections = analysis.get('positive_detections', 0)
                    historical_positives.append(positive_detections > 0)
                    # Estimer la confiance moyenne
                    if 'regions_analyzed' in analysis and analysis['regions_analyzed'] > 0:
                        avg_conf = 50 if positive_detections > 0 else 20  # Estimation
                        historical_confidences.append(avg_conf)

            if not historical_positives:
                return current_analysis_results

            # Calculer les facteurs d'ajustement
            positive_rate = sum(historical_positives) / len(historical_positives)
            avg_historical_confidence = sum(historical_confidences) / len(historical_confidences) if historical_confidences else 50

            logger.info(f"Patient {face_id}: {positive_rate:.0%} positive rate in recent history")

            # Appliquer les ajustements
            adjusted_results = []

            for i, result in enumerate(current_analysis_results):
                adjusted_result = result.copy()
                original_confidence = result.get('confidence', 0)
                original_detected = result.get('leukocoria_detected', False)

                # Facteur d'ajustement basé sur l'historique
                adjustment_factor = 0
                reasoning = []

                # === AJUSTEMENTS BASÉS SUR L'HISTORIQUE ===

                # 1. Si historique de détections positives et détection actuelle
                if positive_rate > 0.3 and original_detected:
                    adjustment_factor += 0.15  # Boost de 15%
                    reasoning.append(f"Consistent with {positive_rate:.0%} positive history")

                # 2. Si pas d'historique positif mais détection actuelle (possiblement nouveau)
                elif positive_rate < 0.2 and original_detected and original_confidence > 60:
                    adjustment_factor += 0.1  # Leger boost car nouveau cas préoccupant
                    reasoning.append("New concerning finding - monitor closely")

                # 3. Si historique positif mais pas de détection actuelle
                elif positive_rate > 0.5 and not original_detected:
                    # Vérifier si la confiance est faible (pourrait être faux négatif)
                    if original_confidence < 40:
                        adjustment_factor += 0.2  # Boost significatif
                        reasoning.append("Low confidence with positive history - possible false negative")
                    else:
                        # Même si pas détecté, augmenter légèrement par précaution
                        adjustment_factor += 0.05
                        reasoning.append("No detection but positive history warrants caution")

                # 4. Ajustement basé sur la stabilité des mesures
                current_confidence = original_confidence
                confidence_diff = abs(current_confidence - avg_historical_confidence)

                if confidence_diff > 30:  # Grande différence par rapport à la moyenne
                    if current_confidence > avg_historical_confidence:
                        adjustment_factor += 0.05
                        reasoning.append("Higher confidence than historical average")
                    else:
                        adjustment_factor -= 0.05
                        reasoning.append("Lower confidence than historical average")

                # 5. Ajustement pour cohérence temporelle
                if len(recent_analyses) >= 3:
                    # Vérifier la tendance des 3 dernières analyses
                    last_three = recent_analyses[-3:]
                    recent_positive_trend = sum(1 for entry in last_three 
                                              if entry.get('analysis_summary', {}).get('positive_detections', 0) > 0)

                    if recent_positive_trend >= 2 and original_detected:
                        adjustment_factor += 0.1
                        reasoning.append("Consistent with recent positive trend")
                    elif recent_positive_trend >= 2 and not original_detected:
                        adjustment_factor += 0.15  # Plus fort car possible faux négatif
                        reasoning.append("Inconsistent with recent positive trend - increase sensitivity")

                # === APPLIQUER L'AJUSTEMENT ===
                if adjustment_factor != 0:
                    new_confidence = original_confidence * (1 + adjustment_factor)
                    new_confidence = max(0, min(100, new_confidence))  # Borner entre 0 et 100

                    adjusted_result['confidence'] = new_confidence
                    adjusted_result['confidence_adjusted'] = True
                    adjusted_result['original_confidence'] = original_confidence
                    adjusted_result['adjustment_factor'] = adjustment_factor
                    adjusted_result['adjustment_reasoning'] = reasoning
                    adjusted_result['patient_history_summary'] = {
                        'positive_rate': positive_rate,
                        'recent_analyses_count': len(recent_analyses),
                        'avg_historical_confidence': avg_historical_confidence
                    }

                    # Réévaluer le risque si changement significatif
                    if abs(new_confidence - original_confidence) > 10:
                        adjusted_result = self._reevaluate_risk_level_with_history(adjusted_result)

                    # Réévaluer la détection si boost significatif
                    if (not original_detected and 
                        new_confidence > 50 and 
                        adjustment_factor > 0.1):
                        adjusted_result['leukocoria_detected'] = True
                        adjusted_result['detection_changed_by_history'] = True
                        reasoning.append("Detection status changed based on patient history")

                    logger.info(f"Patient {face_id}, region {i}: Confidence adjusted from {original_confidence:.1f}% to {new_confidence:.1f}%")
                    logger.info(f"Reasoning: {'; '.join(reasoning)}")

                adjusted_results.append(adjusted_result)

            return adjusted_results

        except Exception as e:
            logger.error(f"Confidence adjustment failed for {face_id}: {e}")
            return current_analysis_results

    def _reevaluate_risk_level_with_history(self, result: Dict) -> Dict:
        """Réévalue le niveau de risque basé sur la nouvelle confiance et l'historique"""
        try:
            confidence = result.get('confidence', 0)
            detected = result.get('leukocoria_detected', False)
            has_history_adjustment = result.get('confidence_adjusted', False)
            adjustment_factor = result.get('adjustment_factor', 0)

            # Seuils ajustés pour les cas avec historique
            if detected:
                if confidence >= 85 or (confidence >= 75 and has_history_adjustment):
                    result['risk_level'] = 'high'
                    result['urgency'] = 'immediate'
                    result['medical_reasoning'] = (result.get('medical_reasoning', '') + 
                        " High confidence with patient history support.")
                elif confidence >= 65 or (confidence >= 55 and has_history_adjustment):
                    result['risk_level'] = 'medium'
                    result['urgency'] = 'urgent'
                    result['medical_reasoning'] = (result.get('medical_reasoning', '') + 
                        " Moderate confidence enhanced by patient history.")
                elif confidence >= 45:
                    result['risk_level'] = 'medium'
                    result['urgency'] = 'soon'
                else:
                    result['risk_level'] = 'low'
                    result['urgency'] = 'routine'
            else:
                # Même pour les non-détectés, vérifier si confiance ajustée suggère surveillance
                if has_history_adjustment and confidence > 30:
                    result['risk_level'] = 'low'
                    result['urgency'] = 'soon'
                    result['recommendation_note'] = 'Continue close monitoring due to patient history'
                elif has_history_adjustment and adjustment_factor > 0.1:
                    # Fort ajustement même sans détection = surveillance renforcée
                    result['urgency'] = 'soon'
                    result['recommendation_note'] = 'Enhanced monitoring recommended based on patient history'

            # Ajouter note spéciale pour les cas ajustés par l'historique
            if has_history_adjustment:
                history_note = f"Confidence adjusted by {adjustment_factor:+.1%} based on patient history. "
                if 'recommendations' in result:
                    result['recommendations'] = history_note + result['recommendations']
                else:
                    result['recommendations'] = history_note + "Continue medical monitoring as advised."

            return result

        except Exception as e:
            logger.error(f"Risk level reevaluation failed: {e}")
            return result

    def get_confidence_adjustment_summary(self, face_id: str) -> Dict:
        """Retourne un résumé des ajustements de confiance pour un patient"""
        try:
            if face_id not in self.analysis_history:
                return {'error': 'No history available'}

            history = self.analysis_history[face_id]

            # Compter les ajustements appliqués
            adjustments_applied = 0
            total_analyses = len(history)

            for entry in history:
                if (entry.get('analysis_summary', {}).get('confidence_adjustments') or 
                    'confidence_adjusted' in str(entry)):
                    adjustments_applied += 1

            # Analyser les tendances
            positive_analyses = sum(1 for entry in history 
                                  if entry.get('has_positive_findings', False))

            summary = {
                'face_id': face_id,
                'total_analyses': total_analyses,
                'adjustments_applied': adjustments_applied,
                'adjustment_rate': (adjustments_applied / total_analyses * 100) if total_analyses > 0 else 0,
                'positive_analyses': positive_analyses,
                'positive_rate': (positive_analyses / total_analyses * 100) if total_analyses > 0 else 0,
                'confidence_system_active': adjustments_applied > 0
            }

            return summary

        except Exception as e:
            logger.error(f"Failed to get adjustment summary for {face_id}: {e}")
            return {'error': str(e)}